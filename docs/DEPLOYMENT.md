# Deployment

The private workspace is live at **https://litereview.onrender.com**.

- [Render web service](https://dashboard.render.com/web/srv-datfmkk9v7es738hgv90): Docker, Starter, Oregon, one instance. Deploys `main` after CI passes; health check `/health`.
- Database: `litereview-db`, paid Basic PostgreSQL 16, 256 MB, 5 GB storage, Oregon. The service uses the internal connection string; external access is disabled with an empty IP allowlist.
- [Environment settings](https://dashboard.render.com/web/srv-datfmkk9v7es738hgv90/env): Modal credentials, the LiteLLM gateway connection, encryption key, and workspace access key are configured. Sign in using `ADMIN_TOKEN` from these settings. Keep `ADMIN_TOKEN` and `SECRET_KEY` stable between deployments.
- `ADMIN_USERS` is `moe-berri`. Gateway base URL is `https://gateway.litellm-sandbox.ai/v1`; the replacement key supplied by the owner is configured. Change the existing model connection through the UI after initial bootstrap.
- No development GitHub token is configured. No demo agents, test runs, or local screenshots were copied to production.

Both paid services stay running when idle; there is no free-tier wake-up delay. This does not imply zero downtime during maintenance or deployments. Sandboxes are created on demand in Modal, with 2 CPU cores and 4 GiB requested per agent. `CONCURRENCY=0` removes the app-level queue cap; a positive value sets an optional workspace-wide limit. Modal quotas and provider rate limits still apply.

## Remaining GitHub setup

Render's existing GitHub installation now has access to `BerriAI/litereview`. That allows deployment; review comments need a separate LiteReview GitHub App.

The guided registration flow was tested against GitHub and its manifest was accepted, but GitHub denied `moe-berri` permission to create an App for BerriAI. A private App for BerriAI repositories must be owned by BerriAI. Do not accept GitHub's offer to create a personal private App for these repositories.

An organization owner can finish either way:

1. Enable the appropriate App registration permission for the workspace owner, who can retry **Connections → GitHub → Create GitHub App → BerriAI**. Or the organization owner can use the same guided flow themselves with authorized access to the LiteReview workspace. It registers the App, stores credentials encrypted, and opens installation automatically.
2. Alternatively, register a private App under [BerriAI's App settings](https://github.com/organizations/BerriAI/settings/apps) and provide its credentials through **Connections → GitHub → Use existing App**. Use the following settings:

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

Install the App on **only `BerriAI/litereview`** for the first test. Refresh repositories in LiteReview, add the repository, create an agent and a group restricted to `moe-berri`, then trigger it on the authorized [draft test PR](https://github.com/BerriAI/litereview/pull/1). Do not merge that test PR.

The web app, gateway, and Modal connectivity are verified. The first production GitHub-triggered review and GitHub sign-in still need verification after App installation. Full evidence is in [VALIDATION.md](VALIDATION.md).
