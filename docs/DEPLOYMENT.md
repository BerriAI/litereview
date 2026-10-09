# Deployment

The private workspace is live at **https://litereview.onrender.com**.

- [Render web service](https://dashboard.render.com/web/srv-datfmkk9v7es738hgv90): Docker, Starter, Oregon, one instance. Deploys `main` after CI passes; health check `/health`.
- Database: `litereview-db`, paid Basic PostgreSQL 16, 256 MB, 5 GB storage, Oregon. The service uses the internal connection string; external access is disabled with an empty IP allowlist.
- [Environment settings](https://dashboard.render.com/web/srv-datfmkk9v7es738hgv90/env): Modal credentials, the LiteLLM gateway connection, encryption key, and workspace access key are configured. Google SSO is required; the workspace access-key login is disabled. Keep the encryption key and all existing credentials stable between deployments.
- `ADMIN_USERS` is `moe-berri`. Gateway base URL is `https://gateway.litellm-sandbox.ai/v1`; the replacement key supplied by the owner is configured. Change the existing model connection through the UI after initial bootstrap.
- No development GitHub token is configured. No demo agents, test runs, or local screenshots were copied to production.

Both paid services stay running when idle; there is no free-tier wake-up delay. This does not imply zero downtime during maintenance or deployments. Sandboxes are created on demand in Modal, with 2 CPU cores and 4 GiB requested per agent. `CONCURRENCY=0` removes the app-level queue cap; a positive value sets an optional workspace-wide limit. Modal quotas and provider rate limits still apply. Runs have no application time or model-call cap unless one is selected in the agent editor’s **Optional limits**. `JOB_TIMEOUT` and `MAX_ROUNDS` are no longer read. Modal requires a maximum sandbox lifetime of 24 hours, which is used when no run time is selected; continuing across replacement sandboxes is not implemented.

## Google SSO

Google sign-in was enabled and verified on October 9, 2026. The shared Internal OAuth client from `moyai-private` now includes `https://litereview.onrender.com/api/auth/google/callback`. LiteReview requires `berri.ai` membership; `moe@berri.ai` is a workspace administrator. A real Google login succeeded, unauthenticated admin APIs returned 401, and the old workspace key was rejected.

Keep `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_ALLOWED_DOMAINS=berri.ai`, `GOOGLE_ADMIN_EMAILS=moe@berri.ai`, and `GOOGLE_SSO_REQUIRED=true`. The Google consent screen uses the shared client’s “Moyai Devin” name. A dedicated LiteReview client can improve that branding later; do not rename the shared client in place.

Teammates sign in with Google and connect their GitHub identity once. People and repository grants remain required. A 12-hour session expiry requires Google sign-in again and preserves the GitHub link. The start page checks access and shows only permitted, verified reviewer commands.

## GitHub setup

Render's existing GitHub installation now has access to `BerriAI/litereview`. That allows deployment; review comments need a separate LiteReview GitHub App.

The saved App is https://github.com/apps/litereview-72beb5. As of October 9, 2026, GitHub setup is reset with Undo available, and there are no connected repositories or saved agents. The existing App belongs to the personal account `moe-berri`: its GitHub installation page only offers that account. GitHub currently returns zero available repositories. `moe-berri` is a BerriAI member and cannot open the organization’s App settings. An organization owner must create/install a BerriAI-owned App (or allow App registration). A private App can only be installed on its owning account.

An organization owner can finish either way:

1. Enable the appropriate App registration permission for the workspace owner, who can retry **Repositories → Connect BerriAI**. Or the organization owner can use the same guided flow themselves with authorized access to the LiteReview workspace. It registers the App, stores credentials encrypted, and opens installation automatically.
2. Alternatively, register a private App under [BerriAI's App settings](https://github.com/organizations/BerriAI/settings/apps) and provide its credentials through **Settings → GitHub App → App credentials**. Use the following settings:

| Setting | Value |
| --- | --- |
| Homepage | `https://litereview.onrender.com` |
| User authorization callback | `https://litereview.onrender.com/api/auth/github/callback` |
| Webhook URL | `https://litereview.onrender.com/api/webhook/github` |
| Setup URL | `https://litereview.onrender.com/repositories` |
| Visibility | Only this account (private, BerriAI-owned) |
| Repository permissions | Contents: read; Metadata: read; Pull requests: write; Issues: write |
| Events | Issue comment |

For manual registration, generate a webhook secret, client secret, and private key. Enter the App ID, PEM private key, webhook secret, client ID, client secret, and App URL into LiteReview's existing-App form. Share credentials through a private channel; never commit them.

Install the App on **only `BerriAI/litereview`** for the first test. Return to **Repositories**, where the selection syncs automatically. Configure and check a reviewer, then verify `moe-berri` in **People** and the repository’s **Review permissions**. New imports use the existing approved-caller policy; restricted and removed grants are preserved. Then trigger it on the authorized [draft test PR](https://github.com/BerriAI/litereview/pull/1). Do not merge that test PR.

The web app, gateway, and Modal connectivity are verified. The first production GitHub-triggered review and GitHub sign-in still need verification after App installation. Full evidence is in [VALIDATION.md](VALIDATION.md).
