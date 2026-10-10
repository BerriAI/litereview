# Deploy a LiteReview workspace

Deploy one workspace for a trusted team, connect a GitHub repository, and get a review posted on a PR. This guide uses the included Render Blueprint: one Docker service, one PostgreSQL database, and on-demand Modal sandboxes.

- [Prepare your accounts](#prepare-your-accounts)
- [Deploy on Render](#deploy-on-render)
- [Get your first review](#get-your-first-review)
- [Operate and update](#operate-and-update)
- [Troubleshoot](#troubleshoot)

Every admitted member can manage shared connections, agents, repository access, and run history. Review code runs on Modal and goes to your selected model provider. Review the [security boundary](../SECURITY.md) before inviting teammates. Render's service and database incur ongoing charges; Modal and models charge for usage. Review time, model-call, and concurrency limits are optional.

## Prepare your accounts

You need a Render account, a Modal account with sandbox capacity, a Google Workspace organization, a model provider or compatible gateway, and permission to create/install a GitHub App for your target repositories. The hosted path uses Google Workspace SSO. For a local UI/API trial without SSO, use [local development](../README.md#local-development).

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

1. Open **Repositories → Connect GitHub**. Choose the account that owns your repositories. Follow the same-tab GitHub registration and installation flow; LiteReview saves App credentials encrypted. Select one repository you control for the initial review. Return to Repositories to import the installation automatically.
2. If you cannot create or install the App, use **Copy setup request** for the appropriate organization owner. Wait for approval, then return and use **Check installation** or **GitHub connection options → Sync repositories**. A private personal App cannot stand in for an organization-owned App.
3. Open **Models → Add connection**, choose your provider or gateway, and enter its API key. For a gateway, use the API base URL supplied by its operator. Save and test the connection.
4. In Repositories, choose **Configure reviewer**, name it `review`, choose a model, and use **Save and check reviewer**. This executes a sample project in a real Modal sandbox and consumes model/Modal credits. It does not post to GitHub. Resolve any reported compatibility or credential error before proceeding.
5. Open the reviewer under **PR agents** to set its instructions and optional maximum run time/model-call limits. Set `CONCURRENCY` in the deployment environment if you want a workspace-wide sandbox cap; `0` leaves concurrency to Modal quotas. Configure provider and Modal budgets in those services.
6. Choose **Start a review**, link your GitHub identity, and copy the permitted command. Post it as a new comment on an open PR in your connected repository:

   ```text
   @litereview review
   ```

   An accepted request gets a 👀 reaction. **Runs** shows progress; completion posts a PR comment with the reviewed commit and a link to the full result. An incomplete result lists the work that could not finish. Read the result before accepting findings as correct.
7. Invite trusted colleagues to your deployment URL. They sign in with Google and link GitHub. **People → Edit access** and each repository's **Review permissions** can restrict who invokes which reviewers; these controls do not remove workspace management access.

For an existing App, see [manual credentials and permissions](../README.md#existing-github-app). For repository-specific build commands, browser checks, skills, and MCP tools, use the in-app **Documentation** page. Add only disposable test credentials to review environments.

## Operate and update

Retain the database, environment settings, and the same `SECRET_KEY`. Losing the database loses configuration and review history. Losing or replacing `SECRET_KEY` prevents decryption of saved connections and invalidates signed sessions. Key rotation with re-encryption is not automated. The upgrade to independent browser sessions preserves currently valid Google sessions with their original expiry; signing in from another browser no longer invalidates them.

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
| Model discovery works but review fails | Confirm the endpoint supports the Responses API, then check tool/image support, provider credits, and the failure details under Runs. |
| Work is incomplete or expensive | Inspect Checks and execution details, narrow instructions/tools, and configure optional limits and upstream budgets. |

See [validation scope](VALIDATION.md) for the distinction between automated checks and live integration evidence.
