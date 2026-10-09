# LiteReview

A private workspace for reusable PR reviewers. Configure each agent’s harness, model, instructions, skills, and tools. Call agents directly or use optional groups to bundle them, then comment on a PR:

```text
@litereview frontend pentest security
```

LiteReview acknowledges an accepted command with a 👀 reaction. Every instance runs in a separate Modal sandbox. When all agents finish, it posts one comment with findings, **No issues found**, or an explicit incomplete result for each agent. Review notes are collapsed and the footer links to the full run and reviewed commit. LiteReview never pushes fixes.

Set **Minimum review time (minutes)** when creating a reviewer or under **PR agents → reviewer → Review instructions**. For example, save `5` for `frontend` and `10` for `security`, then comment:

```text
@litereview frontend security
```

Every instance uses its reviewer's saved minimum, including when called through a group. The clock starts after repository/tool setup. `0` means no minimum and preserves the normal behavior for existing reviewers. Use whole minutes up to `MAX_REVIEW_MINUTES` (120 by default). Changing the setting affects future reviews; accepted jobs keep their saved duration. Comment time options are rejected with instructions to use the UI setting. Private harness checks do not run for this minimum.

The harness checks a monotonic clock, asks the agent to keep investigating and verifying, and rejects early completion. The API independently checks elapsed time. Codex continues the same thread if it returns without submitting; the other harnesses retain their sessions too. This does not invoke the interactive `/goal` slash command. Runs have no LiteReview time or model-call limit by default. To set one, expand **Optional limits** in the agent editor and enter **Maximum run time (minutes)**, **Maximum model calls**, or both. Leave a field blank to remove its limit. Maximum run time includes setup and must exceed the minimum; a minimum never creates a maximum. Groups use each agent’s saved limits, and accepted jobs keep their snapshots. Provider failures and reached limits produce an incomplete review, never a successful early completion. [Modal imposes a 24-hour sandbox lifetime](https://modal.com/docs/guide/sandboxes#timeouts); LiteReview uses that full lifetime when no maximum is selected. Runs do not yet resume across sandbox replacements. Existing MCP selections, skills, visual tools, models, and instructions are preserved.

The private workspace is deployed at [litereview.onrender.com](https://litereview.onrender.com). See [deployment status and GitHub setup](docs/DEPLOYMENT.md) for the remaining organization setup.

## Deploy to Render

1. Create a **Blueprint** in Render from this repository. `render.yaml` provisions one Docker web service and Postgres. If Render cannot see the BerriAI repo, an organization owner may need to approve Render’s GitHub access.
2. Set `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`. The app automatically uses Render’s assigned HTTPS URL; set `PUBLIC_URL` explicitly if using a custom domain. Keep the generated `SECRET_KEY` stable across deploys. Configure Google sign-in as described below. Every admitted workspace member gets the same management access.
3. Sign in with Google. Link GitHub to request reviews, or choose **Workspace** to configure agents and connections first. Every member has the same interface.
4. Open **Repositories → Connect GitHub** (or **Connect BerriAI** when `GITHUB_ORGANIZATION=BerriAI`). Use an App owned by the account that owns your repositories. Continue to GitHub in the same tab; the guided flow registers the App, stores its credentials encrypted, and opens installation. An organization owner or organization-wide App Manager can create the App; installation may need an owner’s approval. A private personal App cannot be installed in an organization.
5. Select repositories on GitHub and return to **Repositories**. LiteReview imports the selection automatically; installation IDs and a second repository picker are unnecessary. Public and private repositories are supported. New selections allow existing approved People to call their permitted reviewers. Open a repository’s **Review permissions** to narrow that access.
6. If no model connection exists, add one under **Models**. Reuse an existing gateway or direct provider where possible. **Configure reviewer** asks for its name and model, then **Save and check reviewer** runs a private harness check using Modal and model credits. Codex is the default; a direct Anthropic connection uses Claude Code. Full instructions, capabilities, skills, and MCP tools remain in the agent editor.
7. Each teammate chooses **Sign in with Google → Connect GitHub**. They appear in **People** automatically and can use all reviewers by default. Use **Edit access** for optional restrictions; no duplicate allowlist is required. The start page shows commands only for permitted repositories and checked reviewers, after verifying current GitHub access. Post the command in a PR; successful setup ends with an actual review result. Provider consent can require additional clicks.

## Resume GitHub setup

**Repositories** checks the App and syncs repository selection when opened or when you return from GitHub. Signed installation webhooks also sync selection. Use **GitHub connection options → Sync repositories** to retry. Failed checks preserve configuration. If GitHub approval is pending, copy the setup request for an organization owner and resume afterward.

A linked GitHub account lets LiteReview recognize your PR comments. It does not establish repository access. **Account** and **Repositories** show identity and repository access separately. When no repositories are connected, workspace pages show **Connect GitHub**, and the start page links directly to setup.

A saved App also does not establish repository access. If it belongs to the wrong account, Repositories identifies that owner and offers the appropriate organization setup. **Copy setup request** includes instructions for an organization owner to assign the organization-wide App Manager role on supported GitHub plans, or create the App themselves. App management and installation are separate permissions. The target organization comes from saved setup or `GITHUB_ORGANIZATION`; it is never inferred from the Google email domain.

To install LiteReview yourself, you need Admin access on the target repositories and the organization’s **Settings → Member privileges → GitHub Apps → Allow repository admins to install GitHub Apps for their repositories** setting enabled. This policy applies to all repository admins. An owner can instead keep installations restricted and select repositories for you. Once installed, use **Add repositories** to update the existing App’s access; no new App is needed. LiteReview’s People and repository grants still restrict who can request reviews.

**Settings → GitHub App** contains credential recovery and **Reset setup**. A reset pauses GitHub reviews while preserving agents, models, MCP connections, skills, repository environments and secrets, access rules, and review history. The App remains on GitHub. **Undo reset** restores the prior connection; saving a new App replaces that backup. Active reviews must finish before resetting.

Sync preserves existing environments and permissions. Removing a repository in LiteReview excludes it from future automatic imports; restore it explicitly under **Previously removed repositories**. Removing or restricting an access grant is never undone by sync. Removing GitHub access leaves the saved environment visible as unavailable. After an App replacement, sync updates verified installation IDs without changing grants.

An expired Google session opens **Sign in to continue**, then restores the previous supported page. The GitHub identity remains linked. A failed GitHub check offers retry instead of asking you to rebuild setup.

## Google Workspace SSO

Use an **Internal** Google OAuth web client for your organization. You can reuse an existing client by adding `https://litereview.onrender.com/api/auth/google/callback` to its authorized redirect URIs; keep its other redirect URIs. A new client uses the same exact URI. For another deployment, replace the origin with `PUBLIC_URL`.

In Render’s LiteReview environment settings, set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_ALLOWED_DOMAINS=berri.ai`, and `GOOGLE_SSO_REQUIRED=true`, then deploy. Register the callback before enabling SSO on an existing workspace: `redirect_uri_mismatch` means the URI is missing from that Google client. Never commit the client secret.

Verify that a workspace account can sign in, an outside account is denied, and the old workspace access key no longer works. Enabling SSO also invalidates existing access-key/GitHub-only sessions. Every admitted Google member can manage agents, groups, skills, shared model/MCP connections, repositories, review access settings, and run history. There is no separate LiteReview administrator role. Shared connections use their connected accounts’ permissions and shared model credits. `GOOGLE_ADMIN_EMAILS` is no longer used; `ADMIN_USERS` only controls legacy GitHub-only sign-in when SSO is disabled. GitHub still controls App installation permissions. Approved Google users appear in **People** automatically. Linked users can call all reviewers unless an explicit person restriction or repository permission disallows them. Existing restrictions are preserved.

The server verifies the Google token signature, issuer, audience, expiry, nonce, verified email, and hosted-domain claim. PKCE and single-use, browser-bound state protect login. Linking verifies GitHub’s stable user ID through OAuth; a typed GitHub username cannot establish membership. GitHub comments require a linked identity with current Google authorization plus repository permission and any explicit per-person restrictions. Sessions expire after `SSO_SESSION_SECONDS` (12 hours by default); sign in again to renew commenting authorization. Signing out revokes it immediately. Google account suspension is recognized at the next sign-in or session expiry; there is no Directory sync. Signed GitHub webhooks and expiring per-job worker tokens remain the machine entry points.

Local development can use `ADMIN_TOKEN` only when no Google client is configured and `GOOGLE_SSO_REQUIRED=false`. A required but incomplete SSO configuration denies access rather than falling back to a workspace key.

Modal images and sandboxes are created on demand using your credentials. The first review builds the image and can take several minutes; subsequent reviews reuse it. No separate Modal app deployment, workflow service, Redis, or storage bucket is required.

## Harnesses

Choose **Built-in**, **Pi**, **OpenCode V1**, **OpenCode V2**, **Codex**, **Claude Code**, or **Deep Agents** alongside the model in each agent's editor. Existing agents keep Built-in. Each sandbox installs and launches only the selected harness; there is no pool of idle harnesses.

Every reviewer includes terminal access to run tests and create reproducers in its disposable checkout. Native editing and subagents are available in harnesses that support them; instructions and skills can request delegation. The main reviewer waits for delegated evidence and submits one result. Browser, computer use, attached skills, and selected MCP tools remain configured per agent. Nothing pushes source changes to GitHub. Skills written for another harness can use equivalent native tools, but custom external tools still need a configured MCP connection. The checkout is an archive of the supplied PR commit; it does not include Git history.

Codex needs a Responses-compatible endpoint. Claude Code needs a Messages-compatible endpoint. A gateway can translate these protocols to compatible models, including Claude with Codex. Pi, OpenCode, and Deep Agents use Chat Completions for gateway/OpenAI connections and Messages for direct Anthropic connections. Browser and desktop checks require image-capable models. Model discovery alone does not establish compatibility: **Test agent** runs the actual selected harness against a sample project, without accessing a GitHub repo or posting comments. It checks sample skill loading and the enabled terminal/browser/desktop tools; it does not execute your review instructions or attached MCP tools. You can stop a test from the editor.

Versions are pinned in `runner/catalog.py` and captured with every job. Runtime updates are application releases. See [harness validation](docs/VALIDATION.md#selectable-harness-validation) for the tested versions and combinations.

## Capabilities

- **Repository**: always available. Read/search the immutable checkout and PR diff.
- **Terminal**: always available. Run tests and commands in the disposable checkout.
- **Browser**: Playwright actions, accessibility structure, and real screenshots sent to the model.
- **Computer use**: screenshots of a private 1280×900 Linux desktop, mouse and keyboard input. It shares Chromium with Browser when both are enabled. Every action returns visual feedback.
- **MCP**: reusable remote Streamable HTTP connections with per-agent tool selection.
- **Skills**: Markdown or ZIP with `SKILL.md` and supporting files. Attach the same skill to several agents. Skills do not grant tools.

Agent and group command names share one case-insensitive unique namespace. **People** and each repository’s **Review permissions** control invocation centrally. With SSO enabled, membership comes from approved Google sign-ins and verified GitHub links. People defaults to all current/future agents and groups. **Edit access** can limit a person to specific selections; an empty selection blocks reviews. **Reset access** restores the default. Repository permissions and any person restriction must allow every name in the command, and the caller must also have GitHub repository access. Without SSO, People remains a manual allowlist. Group grants permit running the group’s members through that group. Connected repositories start with All access. Restrictions follow the Google identity across GitHub renames and relinks, and persist across restarts. Unlinked and expired users remain visible in People with the action needed to regain access; a saved grant never bypasses SSO. Groups are optional references to shared agents. Instance counts belong to group membership. Selecting the same name twice deduplicates it; selecting two groups that share an agent runs each membership independently. Calling an agent directly and through a group runs both selections independently. Accepted runs snapshot their configuration and commit SHA.

## Connect MCP tools

Open **MCP servers** in the left sidebar and choose **Add MCP server**. Enter a name and the Streamable HTTP endpoint URL, keep **OAuth**, and choose **Save & connect**. Approve access with the provider. LiteReview then runs a real connection test and displays the discovered tools. Open a PR agent and select which tools it can use. The account’s selected tools are shared with agents using that connection.

For token-based or public servers, choose **Bearer token** or **No authentication**, then **Save & test**. **Test connection** repeats discovery and shows any failure with a retry path. A saved server or OAuth approval alone does not count as a successful connection test.

OAuth supports discovery, PKCE, automatic client registration where offered, and refresh tokens. If a provider requires a pre-registered app, open **Advanced OAuth settings**, copy the displayed redirect URI into that app, and enter its client ID and optional secret. Authorization and refresh tokens are encrypted at rest and stay in the review service. **Disconnect** removes LiteReview’s local authorization; revoke the upstream grant in the provider’s account settings if needed. **Reconnect** renews it. Cancelling sign-in preserves the server and any previous working authorization.

**MCP servers**, **Skills**, and **Models** are one click away in the sidebar while editing agents. An agent draft survives resource navigation, OAuth redirects, and reloads in the same browser tab. Choose **Resume editing** to return with the newly available tools or skills, or discard the draft. Connection credentials are never saved in browser draft storage.

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

Configure **Repositories → Use existing App** (or **Settings → GitHub App → App credentials** if an App is saved), or set:

- `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY` (PEM, escaped newlines supported), and `GITHUB_WEBHOOK_SECRET`.
- `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` for GitHub sign-in.
- Callback URL: `PUBLIC_URL/api/auth/github/callback`.
- Webhook URL: `PUBLIC_URL/api/webhook/github`; subscribe to **Issue comment** events.
- Repository permissions: **Contents read**, **Metadata read**, **Pull requests write**, **Issues write**. Issues write permits top-level PR conversation comments.

With Google SSO enabled, GitHub OAuth links a caller’s account after Google sign-in and cannot sign in on its own. In local access-key mode, only `ADMIN_USERS` can sign in with GitHub, and `ADMIN_TOKEN` remains available.

## Runtime and boundaries

The FastAPI service serves the built React UI and runs a durable Postgres-backed dispatcher. Each job runs its selected harness in a separate Modal sandbox with Python 3.12, Node 22, Chromium, Playwright, and a Linux desktop. LLM inference happens at the configured provider.

The checkout is a GitHub source archive at the PR's immutable head SHA, rather than a full Git clone. It does not include `.git` history, materialize submodules or Git LFS objects, or preserve symlinks. Repositories that depend on those features need additional support. Executable reviews use the repository's configured setup command, start command, and readiness URL; external services, private package registries, and test accounts still need their own configuration. Modal sandboxes have outbound internet access by default, including package installation and browser navigation. This does not grant access to private networks or credentials.

Long-lived model and MCP keys are encrypted in the database using `SECRET_KEY`. They stay in the API service. Sandboxes use job-scoped tokens to call the configured model/MCP relay. Tokens remain valid only while the job is active; an explicitly selected timeout also expires access after a short result-reporting grace period. GitHub credentials never enter the checkout. Optional repository test secrets are deliberately made available to the sandbox; use test credentials only.

For a small v1, screenshots are private, bounded Postgres artifacts rather than a separate storage service. Each job stores at most 20 PNGs, 2 MB each. Logs are bounded to 200 events. Repository archives are limited to 50 MB compressed / 250 MB expanded; PRs to 300 changed files / a 2 MB diff. Sandboxes launch on demand across reviews by default. Set `CONCURRENCY` to a positive number for an optional workspace-wide cap; `0` leaves concurrency to Modal account quotas and provider rate limits. Each sandbox requests 2 CPU cores and 4 GiB of memory. Time and model-call budgets are optional per-agent settings. Legacy `JOB_TIMEOUT` and `MAX_ROUNDS` environment values no longer impose defaults. Existing reviewers have no minimum by default. Keep Postgres backups enabled; deleting the database loses settings and run history.

GitHub retries are deduplicated by delivery ID. Pending jobs survive API restarts; completed jobs are never rerun automatically. Sandboxes are terminated on completion. The dispatcher reattaches to recorded active sandboxes after API restarts and reports an incomplete result if a sandbox exits without a result. Modal’s 24-hour lifetime still applies. Acknowledgment retries reuse the same eyes reaction, and final-publication retries recover the marked result comment. Existing progress comments from older versions are updated when their runs finish. Results always name the reviewed SHA; later pushes are called out.

This v1 posts summary comments, not resolvable inline review threads. It has no automatic fixes, push triggers, fusion/judge agent, persistent chat, local stdio MCP servers, or plugin marketplace. See [SPEC.md](SPEC.md) for the product decisions and [docs/VALIDATION.md](docs/VALIDATION.md) for actual test evidence and external setup boundaries.
