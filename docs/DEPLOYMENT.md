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

The saved App is https://github.com/apps/litereview-72beb5. The October 9, 2026 walkthrough found the App restored and the Google account linked to GitHub as `moe-berri`, with no connected repositories or saved agents. The App belongs to the personal account `moe-berri`; a private App can only be installed on its owning account, so it cannot connect BerriAI repositories. GitHub reports `moe-berri` as an active BerriAI member. An organization owner or organization-wide App Manager must create a BerriAI-owned App, then someone with installation permission must select repositories.

An organization owner can finish either way:

1. On plans that offer the organization-wide App Manager role, open **BerriAI → Settings → Organization roles → Role assignments → New role assignment**, select `moe-berri` and **App Manager**, then click **Add new assignment**. This grants creation and management of all BerriAI-owned Apps, not installation permission. The workspace owner can retry **Repositories → Connect BerriAI repositories**. See [GitHub’s role assignment steps](https://docs.github.com/en/organizations/managing-peoples-access-to-your-organization-with-roles/using-organization-roles) and [App Manager permissions](https://docs.github.com/en/organizations/managing-peoples-access-to-your-organization-with-roles/permissions-of-predefined-organization-roles). If the role is unavailable, the organization owner can use the same guided flow with authorized LiteReview workspace access. It registers the App, stores credentials encrypted, and opens installation automatically.
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

To let `moe-berri` install the App and add repositories without repeated owner approval, an organization owner can enable **Settings → Member privileges → GitHub Apps → Allow repository admins to install GitHub Apps for their repositories**. This policy applies to all repository admins. `moe-berri` already has Admin access on `BerriAI/litereview`; grant Admin only on other repositories they should manage. The App requests no organization or repository-administration permissions. See [GitHub’s installation policy](https://docs.github.com/en/organizations/managing-programmatic-access-to-your-organization/limiting-oauth-app-and-github-app-access-requests-and-installations).

Install the App on **only `BerriAI/litereview`** for the first test. Return to **Repositories**, where the selection syncs automatically. Use **Add repositories** for later selections on the same installation. Configure and check a reviewer, then verify `moe-berri` in **People** and the repository’s **Review permissions**. New imports use the existing approved-caller policy; restricted and removed grants are preserved. Then trigger it on the authorized [draft test PR](https://github.com/BerriAI/litereview/pull/1). Do not merge that test PR.

The web app, gateway, and Modal connectivity are verified. Production shows the linked GitHub identity, but the first production review through a BerriAI-owned App still needs verification after installation. Full evidence is in [VALIDATION.md](VALIDATION.md).
