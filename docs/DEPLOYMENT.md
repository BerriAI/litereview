# Deployment

The private workspace is live at **https://litereview.onrender.com**.

- [Render web service](https://dashboard.render.com/web/srv-datfmkk9v7es738hgv90): Docker, Starter, Oregon, one instance. Deploys `main` after CI passes; health check `/health`.
- Database: `litereview-db`, paid Basic PostgreSQL 16, 256 MB, 5 GB storage, Oregon. The service uses the internal connection string; external access is disabled with an empty IP allowlist.
- [Environment settings](https://dashboard.render.com/web/srv-datfmkk9v7es738hgv90/env): Modal credentials, the LiteLLM gateway connection, encryption key, and workspace access key are configured. Sign in using `ADMIN_TOKEN` from these settings. Keep `ADMIN_TOKEN` and `SECRET_KEY` stable between deployments.
- `ADMIN_USERS` is `moe-berri`. Gateway base URL is `https://gateway.litellm-sandbox.ai/v1`; the replacement key supplied by the owner is configured. Change the existing model connection through the UI after initial bootstrap.
- No development GitHub token is configured. No demo agents, test runs, or local screenshots were copied to production.

Both paid services stay running when idle; there is no free-tier wake-up delay. This does not imply zero downtime during maintenance or deployments. Sandboxes are created on demand in Modal, with 2 CPU cores and 4 GiB requested per agent. `CONCURRENCY=0` removes the app-level queue cap; a positive value sets an optional workspace-wide limit. Modal quotas and provider rate limits still apply.

## Google SSO rollout

Checked October 8, 2026: LiteReview has no Google OAuth environment variables. Render's `moyai-private` service already has an Internal Google OAuth client restricted to `berri.ai`. That client currently returns `redirect_uri_mismatch` for LiteReview. The signed-in `moe@berri.ai` account cannot access its Google Cloud project (`protean-chassis-510202-k5`).

A Google Cloud administrator can reuse that client:

1. Open **Google Auth Platform → Clients** in the existing project and add `https://litereview.onrender.com/api/auth/google/callback` to the client's **Authorized redirect URIs**. Retain Moyai's existing callback. A dedicated Internal web client is also supported.
2. Copy the matching `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` privately into [LiteReview's Render environment](https://dashboard.render.com/web/srv-datfmkk9v7es738hgv90/env). Set `GOOGLE_ALLOWED_DOMAINS=berri.ai`, `GOOGLE_ADMIN_EMAILS=moe@berri.ai`, and `GOOGLE_SSO_REQUIRED=true`. Keep `SECRET_KEY` and the existing database/model/Modal settings.
3. Deploy and verify **Sign in with Google** using a real workspace account. Check that an outside account and the old owner key cannot access the APIs. Existing owner/GitHub sessions are invalid once Google SSO is configured. Missing Google settings fail closed when SSO is required.
4. Each caller opens **Your LiteReview account → Link GitHub account**. Existing People and Repository grants remain required; Google sign-in alone does not grant permission to call agents. Sign in again when the 12-hour authorization expires.

The code supports this rollout, but Google sign-in must not be described as live until the callback is registered and a real account has completed it. Do not enable SSO on the running service while the callback still fails.

## GitHub setup

Render's existing GitHub installation now has access to `BerriAI/litereview`. That allows deployment; review comments need a separate LiteReview GitHub App.

The production service reports a configured GitHub App at https://github.com/apps/litereview-72beb5. As of October 8, 2026, it has no connected repositories or saved agents. The existing App belongs to the personal account `moe-berri`: its GitHub installation page only offers that account. GitHub currently returns zero available repositories. `moe-berri` is a BerriAI member and cannot open the organization’s App settings. An organization owner must create/install a BerriAI-owned App (or allow App registration). A private App can only be installed on its owning account.

An organization owner can finish either way:

1. Enable the appropriate App registration permission for the workspace owner, who can retry **Connections → GitHub → Connect an organization instead → BerriAI**. Or the organization owner can use the same guided flow themselves with authorized access to the LiteReview workspace. It registers the App, stores credentials encrypted, and opens installation automatically.
2. Alternatively, register a private App under [BerriAI's App settings](https://github.com/organizations/BerriAI/settings/apps) and provide its credentials through **Connections → GitHub → App settings and recovery → Update App credentials**. Use the following settings:

| Setting | Value |
| --- | --- |
| Homepage | `https://litereview.onrender.com` |
| User authorization callback | `https://litereview.onrender.com/api/auth/github/callback` |
| Webhook URL | `https://litereview.onrender.com/api/webhook/github` |
| Setup URL | `https://litereview.onrender.com/connections` |
| Visibility | Only this account (private, BerriAI-owned) |
| Repository permissions | Contents: read; Metadata: read; Pull requests: write; Issues: write |
| Events | Issue comment |

For manual registration, generate a webhook secret, client secret, and private key. Enter the App ID, PEM private key, webhook secret, client ID, client secret, and App URL into LiteReview's existing-App form. Share credentials through a private channel; never commit them.

Install the App on **only `BerriAI/litereview`** for the first test. Return to LiteReview, where repository access is checked automatically. Choose **Connect** beside the repository, create an agent, and use **Access** to grant `moe-berri` and the repository access to it. Then trigger it on the authorized [draft test PR](https://github.com/BerriAI/litereview/pull/1). Do not merge that test PR.

The web app, gateway, and Modal connectivity are verified. The first production GitHub-triggered review and GitHub sign-in still need verification after App installation. Full evidence is in [VALIDATION.md](VALIDATION.md).
