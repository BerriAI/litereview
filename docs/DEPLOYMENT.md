# Deploy a LiteReview workspace

Deploy one workspace for a trusted team, connect a GitHub repository, and get a review posted on a PR. This guide uses the included Render Blueprint: one Docker service, one PostgreSQL database, and on-demand Modal sandboxes.

- [Prepare your accounts](#prepare-your-accounts)
- [Deploy on Render](#deploy-on-render)
- [Get your first review](#get-your-first-review)
- [Operate and update](#operate-and-update)
- [Troubleshoot](#troubleshoot)

Every admitted member can manage shared connections, agents, repository access, and run history. Review code runs on Modal and goes to your selected model provider. Review the [security boundary](../SECURITY.md) before inviting teammates. Render's service and database incur ongoing charges; Modal and models charge for usage. Review time, model-call, and concurrency limits are optional.

## Prepare your accounts

You need a Render account, a Modal account with sandbox capacity, a Google Workspace organization, a model provider or compatible gateway, and permission to create/install a GitHub App for your target repositories. The hosted path uses Google Workspace SSO. For a local UI/API trial without SSO, use [local development](DEVELOPMENT.md).

1. **Modal:** create a token for the account/workspace that will run reviews. Follow [Modal's token setup](https://modal.com/docs/reference/cli/token). With uv installed, the command is:

   ```sh
   uvx --from 'modal==1.5.5' modal token new
   ```

   Complete the browser flow. The CLI stores `token_id` and `token_secret` in `~/.modal.toml`; enter them privately as `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` in Render. Do not commit that file. LiteReview creates its Modal app and sandbox images on demand; there is no separate Modal app deployment.
2. **Google:** in [Google Auth Platform](https://console.cloud.google.com/auth/overview), select a project owned by your Workspace organization. Configure the app branding and an **Internal** audience, then create an OAuth client with application type **Web application** under **Clients**. Save the client ID and secret for Render. You will add the exact callback URL once Render assigns your deployment URL.
3. **Model:** obtain an API key for OpenAI or a gateway that supports the Responses API. Add it through LiteReview's **Models** page after signing in. All reviewers use Codex; Anthropic models require a Responses-compatible gateway. Model discovery alone does not establish compatibility.
4. **GitHub:** identify the account that owns the repositories. For organization repositories, an organization owner or organization-wide App Manager must create the App; installation can require an owner's approval. A private personal App cannot be installed into an organization. LiteReview's guided setup creates the App after deployment.

## Deploy on Render

1. Fork this repository. In the [Render dashboard](https://dashboard.render.com/), choose **New → Blueprint**, connect your fork, select its deployment branch, and use `render.yaml`. The Blueprint provisions a paid Starter Docker service and Basic Postgres database in Oregon, with one service instance. Grant Render access only to the repository needed for deployment.
2. Set the requested environment values:

   | Variable | Value |
   | --- | --- |
   | `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` | Token from your Modal workspace |
   | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Your Google web OAuth client |
   | `GOOGLE_ALLOWED_DOMAINS` | Comma-separated Workspace domains you control, for example `example.com` |
   | `ADMIN_EMAILS` | Google emails allowed to view spend, for example `owner@example.com,finance@example.com`; blank hides spend from every SSO member |
   | `GITHUB_ORGANIZATION` | Your GitHub organization slug, or blank to choose the owner during setup |

   The Blueprint sets `GOOGLE_SSO_REQUIRED=true`, generates `SECRET_KEY` and `ADMIN_TOKEN`, and wires `DATABASE_URL` to Postgres. The access key cannot bypass required SSO. Keep the encryption key stable across deploys. Existing Blueprint users must keep their own domain and organization values when syncing this configuration.
3. Deploy and copy the service's assigned HTTPS URL. LiteReview automatically uses `RENDER_EXTERNAL_URL`. If using a custom domain, configure it in Render and set `PUBLIC_URL` to that HTTPS origin without a path.
4. In your Google OAuth client's **Authorized redirect URIs**, add exactly `https://YOUR-LITEREVIEW-HOST/api/auth/google/callback`, replacing the origin with your service URL. Keep any redirect URIs used by other applications. Open the same origin in your browser and choose **Sign in with Google**.
5. Verify the service and sign-in:

   ```sh
   # Replace this with the URL assigned to your service.
   LITEREVIEW_URL=https://YOUR-LITEREVIEW-HOST
   curl --fail "$LITEREVIEW_URL/health"
   ```

   Expect `{"status":"ok"}`. Sign in with an allowed Workspace account and confirm that an outside account is denied. In a private browser window, workspace data must require sign-in. A healthy API is an intermediate check; the next section verifies an actual review.

Modal workers must reach this public HTTPS origin. Keep one API worker and one service instance. PostgreSQL external access is disabled by the Blueprint's empty IP allowlist. The first review builds a sandbox image and can take several minutes.

## Get your first review

Stay in your deployed workspace for these steps. Use one repository and one model first. The default reviewer already has instructions for finding actionable defects; skills, MCP tools, Fusion, and automatic reviews can wait.

### 1. Connect a repository

Open **Repositories → Connect GitHub** (the button names your organization when configured). Choose the account that owns your repositories. Follow GitHub's registration and installation flow, select one repository you control, and return to LiteReview. The repository should appear automatically. LiteReview creates and stores the App credentials for you; there is no installation ID to copy or second repository picker.

For organization repositories, an owner or organization-wide App Manager must create the App, and installation may need an owner's approval. If blocked, use **Copy setup request**, then return after approval and choose **Check installation** or **GitHub connection options → Sync repositories**. If you already have an App, use [existing App setup](GITHUB.md#existing-github-app).

### 2. Connect a model

Open **Models → Add connection**. If a suitable connection already exists, reuse it and continue to the next step.

- Give the connection a name you will recognize, such as `Review models`.
- Under **Connection type**, choose **OpenAI** for a direct OpenAI key and leave **API base URL** blank. For another provider, choose **OpenAI-compatible gateway** and enter the API base URL supplied by its operator, including `/v1` if required. Use the base URL, not a full `/responses` endpoint or dashboard URL. The LiteReview API service must be able to reach it.
- Paste the provider or gateway key into **API key** and choose **Save & test**. Confirm that available models load. The next step tests an actual model request and the review tools.

All reviewers use Codex and need the Responses API. For Anthropic models, use a gateway that supports this API; a direct Anthropic connection cannot run a reviewer. Browser and computer-use checks also need an image-capable model.

### 3. Check your first reviewer

Return to **Repositories → Configure reviewer**. Keep the default **Reviewer name** (`reviewer`), choose an available **Model**, and choose **Save and check reviewer**. If you have several connections, choose the one you just tested. Leave **Minimum review time (minutes)** at `0` for this first check.

This runs Codex on a sample project in a real Modal sandbox and consumes Modal/model credits. It does not access your repository or post to GitHub. The first check may take several minutes while Modal builds its image. Wait for **Ready to review** in Repositories before continuing. If the check fails, inspect its error and retry the saved reviewer; you do not need another agent.

The first reviewer becomes the default automatically. No maximum run time or model-call limit is set. If you need a limit before your first PR review, open **PR agents → reviewer → Review instructions → Optional limits** and set it there. Configure spending budgets with your model provider and Modal. The [reviewer guide](REVIEWERS.md#review-instructions) explains how limits and minimum review time interact.

### 4. Review an open PR

Choose **Start a review** in Repositories and **Connect GitHub** if your personal identity is not linked. Use the GitHub account you will comment from. Installing the workspace's App and linking your own identity are separate steps; every teammate links their own account.

Choose your repository and reviewer if prompted, then choose **Copy comment**. Post it as a **new comment** on an open PR in the connected repository. For your default reviewer:

```text
@litereview
```

**Success:** LiteReview reacts with 👀 and posts a progress comment. That comment becomes a review report with the reviewed commit and links to findings. Follow **View review** or open **Runs** to inspect the checks, screenshots, and activity. A saved connection or successful sample check is not yet a PR review.

A ✅ marks completion, not an issue-free PR. **Review incomplete** lists unfinished work; completed reviews can still identify bugs or skipped checks. Read the report before accepting its findings. See [troubleshooting](#troubleshoot) if no review appears.

### After your first review

- Send trusted colleagues your deployment URL. They choose **Sign in with Google → Connect GitHub**; they reuse the shared setup. **People → Edit access** and repository **Review permissions** can restrict which reviewers they invoke, but do not remove workspace management access.
- Customize [review instructions, models, skills, and tools](REVIEWERS.md). For repository-specific setup/start commands and readiness URLs, open **Repositories**, edit the repository, and use the in-app **Documentation** page. Add only disposable test credentials to review environments.
- Set `CONCURRENCY` in Render if you want to cap simultaneous sandboxes; `0` leaves concurrency to Modal quotas. Each configured agent can also have optional run time and model-call limits.
- Open **Spend** as a configured admin to inspect recorded model costs. If the page is missing, check [spend admins](#spend-admins).

## Operate and update

### Spend admins

In Render, open your LiteReview service → **Environment** and set `ADMIN_EMAILS` to the comma-separated Google email addresses that should see costs, for example `owner@example.com,finance@example.com`. Use full addresses from the allowed Workspace domains. Save and redeploy to load the setting. Preserve any existing addresses when adding an admin. Matching is case-insensitive; an empty list grants nobody access to spend. GitHub linking is not required for this permission. `GOOGLE_ADMIN_EMAILS` is not used.

After deployment, refresh LiteReview as a listed admin: **Spend** and **Runs → Model cost** should be visible. Sign in as an unlisted member and confirm both are absent; opening `/spend` shows an access restriction, and `/api/spend` returns 403. Members can still read review results and manage the workspace. Remove an address and redeploy to revoke its spend access, including for existing sessions. Without Google SSO, access-key owners and accounts admitted by `ADMIN_USERS` retain spend access.

### Maintenance

Retain the database, environment settings, and the same `SECRET_KEY`. Losing the database loses configuration and review history. Losing or replacing `SECRET_KEY` prevents decryption of saved connections and invalidates signed sessions. Key rotation with re-encryption is not automated. Each browser has its own session; signing in from another browser does not invalidate an existing session.

- Enable database backups in your hosting provider and verify a restore into a separate, access-restricted deployment using the matching encryption key. Keep `DISPATCHER_ENABLED=false` there while inspecting data so restored queued jobs cannot launch or publish reviews.
- Monitor database size, service memory, provider spend, and Modal usage. Screenshots, activity logs, uploads, and review results have no automatic retention policy or application size cap. Choose per-agent limits and a concurrency cap to suit your budget.
- Offboard people explicitly in LiteReview: an expired browser session or suspended Google account does not automatically revoke their linked GitHub review access. See [offboarding](../SECURITY.md#intended-deployment-boundary).
- Update provider/MCP credentials through their connection settings. Follow your provider's revocation procedure for compromised credentials. Preserve working authorization until a replacement has been tested where possible.

During beta, deploy a reviewed commit from your fork. Before updating, record the current commit and configuration and take a database backup. Wait for active reviews to finish, run CI on the target commit, then use Render's **Manual Deploy → Deploy a specific commit** or merge into the configured auto-deploy branch. The Blueprint deploys after checks pass. Keep one worker/instance and retain existing environment values.

After deployment, check `/health`, sign-in, saved connections/repositories, and a representative review. Startup applies the schema adjustments implemented in `app/db.py`; there is no general downgrade migration framework. If recovery requires an older schema, restore the matching database backup and application commit together in an isolated deployment before routing traffic to it. Rolling back application code alone does not prove database compatibility.

## Troubleshoot

| Symptom | Check and recovery |
| --- | --- |
| `redirect_uri_mismatch` | Register the exact browser origin plus `/api/auth/google/callback` in the configured Google client. Check for a stale client ID or hostname. |
| Google sign-in is unavailable or denied | Check the client ID/secret, Internal audience, `GOOGLE_ALLOWED_DOMAINS`, and the account's Workspace domain. Required but incomplete SSO fails closed. |
| Request origin does not match | Make `PUBLIC_URL` match the HTTPS origin used in your browser. Restart after changing the environment. |
| GitHub App exists but no repositories appear | Check the App owner, pending organization approval, and installed repository selection; then sync in Repositories. |
| Reviewer check fails before execution | Check Modal credentials, account capacity, and whether Modal can reach `PUBLIC_URL` over HTTPS. Retry the saved reviewer check. |
| No reaction or run after a PR comment | Confirm that **Start a review** shows a permitted command, use the linked GitHub account, and post a new comment on an open PR in a connected repository. Check the App's **Issue comment** subscription and **Advanced → Recent deliveries** on GitHub. A rejected webhook never reaches the review worker; inspect the delivery response and hosting logs. |
| Spend is missing for an admin | Set `ADMIN_EMAILS` to the verified Google email, preserve other admins, and redeploy. `ADMIN_USERS` does not grant spend access with Google SSO. |
| Model discovery works but review fails | Confirm the endpoint supports the Responses API, then check tool/image support, provider credits, and the failure details under Runs. |
| Work is incomplete or expensive | Inspect Checks and execution details, narrow instructions/tools, and configure optional limits and upstream budgets. |

See [validation scope](VALIDATION.md) for the distinction between automated checks and live integration evidence.
