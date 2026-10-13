# Connect GitHub and manage access

For a new workspace, follow [Get your first review](DEPLOYMENT.md#get-your-first-review). This guide covers existing Apps, interrupted setup, access, and automatic reviews.

- [Resume GitHub setup](#resume-github-setup)
- [Google Workspace SSO](#google-workspace-sso)
- [Use an existing GitHub App](#existing-github-app)
- [Run reviews automatically](#run-reviews-automatically)

## Resume GitHub setup

**Repositories** checks the App and syncs repository selection when opened or when you return from GitHub. Signed installation webhooks also sync selection. Use **GitHub connection options → Sync repositories** to retry. Failed checks preserve configuration. If GitHub approval is pending, copy the setup request for an organization owner and resume afterward.

A linked GitHub account lets LiteReview recognize your PR comments. It does not establish repository access. **Account** and **Repositories** show identity and repository access separately. When no repositories are connected, workspace pages show **Connect GitHub**, and the start page links directly to setup.

A saved App also does not establish repository access. If it belongs to the wrong account, Repositories identifies that owner and offers the appropriate organization setup. **Copy setup request** includes instructions for an organization owner to assign the organization-wide App Manager role on supported GitHub plans, or create the App themselves. App management and installation are separate permissions. The target organization comes from saved setup or `GITHUB_ORGANIZATION`; it is never inferred from the Google email domain.

To install LiteReview yourself, you need Admin access on the target repositories and the organization’s **Settings → Member privileges → GitHub Apps → Allow repository admins to install GitHub Apps for their repositories** setting enabled. This policy applies to all repository admins. An owner can instead keep installations restricted and select repositories for you. Once installed, use **Add repositories** to update the existing App’s access; no new App is needed. LiteReview’s People and repository grants still restrict who can request reviews.

**Settings → GitHub App** contains credential recovery and **Reset setup**. A reset pauses GitHub reviews while preserving agents, models, MCP connections, skills, repository environments and secrets, access rules, and review history. The App remains on GitHub. **Undo reset** restores the prior connection; saving a new App replaces that backup. Active reviews must finish before resetting.

Sync preserves existing environments and permissions. Removing a repository in LiteReview excludes it from future automatic imports; restore it explicitly under **Previously removed repositories**. Removing or restricting an access grant is never undone by sync. Removing GitHub access leaves the saved environment visible as unavailable. After an App replacement, sync updates verified installation IDs without changing grants.

Each browser stays signed in independently. Signing in or out in another browser does not close your session. When your session expires, **Sign in to continue** returns you to the page you opened, including a specific run. The GitHub identity remains linked. A failed GitHub check offers retry instead of asking you to rebuild setup.

## Google Workspace SSO

Use an **Internal** Google OAuth web client for your Google Workspace organization. Register `https://YOUR-LITEREVIEW-HOST/api/auth/google/callback` as an authorized redirect URI, replacing the origin with your deployment's actual `PUBLIC_URL`. If reusing a client, keep its other redirect URIs. See the [credential setup steps](DEPLOYMENT.md#prepare-your-accounts).

In Render’s LiteReview environment settings, set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_ALLOWED_DOMAINS` to your domain (for example, `example.com`), and `GOOGLE_SSO_REQUIRED=true`, then deploy. Register the callback before enabling SSO on an existing workspace: `redirect_uri_mismatch` means the URI is missing from that Google client. Never commit the client secret.

Verify that a workspace account can sign in, an outside account is denied, and the old workspace access key no longer works. Enabling SSO also invalidates existing access-key/GitHub-only sessions. Every admitted Google member can manage agents, groups, skills, shared model/MCP connections, repositories, review access settings, and run history. Only verified Google emails listed in `ADMIN_EMAILS` can view spend; this admin distinction does not change other management permissions. Shared connections use their connected accounts’ permissions and shared model credits. `GOOGLE_ADMIN_EMAILS` is no longer used; `ADMIN_USERS` only controls legacy GitHub-only sign-in when SSO is disabled. GitHub still controls App installation permissions. Approved Google users appear in **People** automatically. Linked users can call all reviewers unless an explicit person restriction or repository permission disallows them. Existing restrictions are preserved.

The server verifies the Google token signature, issuer, audience, expiry, nonce, verified email, and hosted-domain claim. PKCE and single-use, browser-bound state protect login. Linking verifies GitHub’s stable user ID through OAuth; a typed GitHub username cannot establish membership. Once linked, that GitHub account can request reviews without signing in again. Browser sessions expire after `SSO_SESSION_SECONDS` (12 hours by default); expiry and signing out do not revoke the GitHub link. Every request still checks the linked identity against the configured Google client and allowed domains, GitHub repository permission, and any explicit per-person restrictions. To stop a person from requesting reviews, use **People → Edit access** and leave the selection empty. Unlinking GitHub also stops requests from that account. There is no Google Directory sync: suspending a Google account prevents future Google sign-ins but does not automatically revoke its existing GitHub review access. Signed GitHub webhooks and expiring per-job worker tokens remain the machine entry points.

Local development can use `ADMIN_TOKEN` only when no Google client is configured and `GOOGLE_SSO_REQUIRED=false`. A required but incomplete SSO configuration denies access rather than falling back to a workspace key.

Modal images and sandboxes are created on demand using your credentials. The first review builds the image and can take several minutes; subsequent reviews reuse it. No separate Modal app deployment, workflow service, Redis, or storage bucket is required.

## Existing GitHub App

Configure **Repositories → Use existing App** (or **Settings → GitHub App → App credentials** if an App is saved), or set:

- `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY` (PEM, escaped newlines supported), and `GITHUB_WEBHOOK_SECRET`.
- `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` for GitHub sign-in.
- Callback URL: `PUBLIC_URL/api/auth/github/callback`.
- Webhook URL: `PUBLIC_URL/api/webhook/github`; subscribe to **Issue comment**, **Pull request**, and **Pull request review comment** events.
- Repository permissions: **Contents read**, **Metadata read**, **Pull requests write**, **Issues write**. Issues write permits top-level PR conversation comments.

With Google SSO enabled, GitHub OAuth links a caller’s account after Google sign-in and cannot sign in on its own. In local access-key mode, only `ADMIN_USERS` can sign in with GitHub, and `ADMIN_TOKEN` remains available.

## Run reviews automatically

Open **Repositories → repository options → Automatic reviews**, then:

1. Choose **Add rule** and select an agent or group.
2. Leave **PR authors** blank for everyone, or enter one or more GitHub usernames separated by commas. This matches the PR author, including bot accounts such as `dependabot[bot]`, rather than the person pushing a commit. The list is saved with the rule.
3. Choose **Run when**: **PR opened**, **Ready for review**, **New commits pushed**, **PR reopened**, or **Label added**. For a label trigger, enter the required **Trigger label**.
4. Save the repository. Enabled rules start paid reviews and post results on GitHub. They apply to external contributors and bots without requiring the author to sign in to LiteReview; repository **Review permissions** still limit which reviewers can run.

Apps created by this version subscribe to **Pull request** events. For an existing App, open its GitHub **Permissions & events**, enable **Pull request**, and save. Keep **Issue comment** enabled for mentions. Return to **Repositories** to refresh the connection check.

Enable **Pull request review comment** for [finding-thread follow-ups](REVIEWERS.md#discuss-a-finding). This uses the existing **Pull requests write** permission. A valid rebuttal can resolve the finding's thread automatically.

Trigger a matching event on an open, non-draft PR. The resulting entry in **Runs** shows the automatic trigger and reviewed commit. Spend is attributed to the PR author. If nothing starts, check the App event subscription, enabled rule, author/label filter, and repository permissions. Saving a rule does not review existing PRs retroactively.

Drafts, closed PRs, and obsolete webhook deliveries are skipped. Overlapping rules run each agent instance at most once per PR commit; groups retain their requested instance count. Failed attempts are not automatically retried on the same commit. Use an explicit `@litereview agent-name` comment to request another review. Disabling a rule stops future triggers; accepted runs keep their configuration and continue.

**PR agents** lists the most-used agents first. **Times used** counts PR runs, including group use and incomplete runs, once per agent per run. Private model checks and fusion branches do not increase the count. Ties sort by name.

## Add another organization

Open **Repositories → Add organization → Choose organization in GitHub**. Select the organization, choose its repositories, and approve installation of the existing LiteReview App. When you return, LiteReview syncs access and shows the organization in its own section below the others. Use **Check installation** if it has not appeared. Each organization’s **Manage GitHub access** action opens its own installation.

If GitHub only shows the App owner, the App owner must change **General → Where can this GitHub App be installed? → Any account** in GitHub App settings. An organization owner may also need to approve installation. This does not require new App credentials. Organizations connected to this deployment share the workspace’s agents, members, model connections, and spend.
