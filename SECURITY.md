# Security policy

## Report a vulnerability

Email [support@berri.ai](mailto:support@berri.ai) with the subject **LiteReview security report**. Include the affected commit, deployment configuration, impact, and a minimal reproduction using a system you control. Redact credentials, customer code, and personal data. Please keep exploit details out of public issues and pull requests until a fix is available.

Security fixes target the current `main` branch during the beta. Older snapshots do not have a separate maintenance commitment. This policy does not create a bounty or response-time guarantee.

## Intended deployment boundary

LiteReview is a single-workspace application for trusted teams. Every admitted workspace member can manage shared model/MCP connections, agents, repositories, permissions, and review history. It does not isolate mutually untrusted tenants or provide a read-only member role. A member can exercise a shared connection's permissions through an agent even though its saved credential is not returned by the UI.

Spend and admin-role changes are restricted to admins in both the interface and API. With Google SSO, admins are verified addresses in the deployment's `ADMIN_EMAILS` allowlist or verified Google identities granted an admin role by another admin in People. Saved roles bind to the Google identity, survive restarts and GitHub relinks, and are rechecked on every request. Clearing the allowlist does not revoke roles granted in People. Google domain membership, GitHub permissions, and People review grants do not confer this role. Without SSO, the workspace access-key owner and `ADMIN_USERS` accounts are admins. Other workspace management permissions remain shared. See [admin configuration and revocation](docs/DEPLOYMENT.md#spend-admins).

Use Google Workspace SSO for a hosted team deployment, restrict `GOOGLE_ALLOWED_DOMAINS` to domains you control, and protect the service with HTTPS. Workspace access-key mode is intended for local development. Keep `SECRET_KEY` private and stable; it protects both encrypted credentials and signed sessions. Back it up separately from the database.

Each Google browser session has its own expiry and revocable token. **Sign out** revokes the current browser session; other browsers remain signed in until they sign out or reach `SSO_SESSION_SECONDS` (12 hours by default).

The remote management MCP delegates Google login to that same SSO flow, then asks for consent to the MCP client's access with the user's current workspace permissions. Google tokens are not passed to clients. MCP access tokens are opaque, hashed at rest, expire after one hour, and are bound to the deployment's `/api/mcp` resource. Refresh tokens rotate; replay revokes the grant. Grants expire after 30 days and can be revoked in **Settings → Connect an AI assistant**. Browser sign-out does not revoke them. Every request checks the saved identity's domain, audience, grant and current role. Google account suspension is not polled; revoke existing connections explicitly during offboarding. Client names and callbacks on the consent screen are supplied by the requesting client, not verified endorsements.

Google sign-out and browser-session expiry do not revoke linked GitHub review access. Suspending a Google account does not automatically revoke that link. To offboard a reviewer, use **People → Edit access** and leave the selection empty, or unlink their GitHub identity. GitHub repository permissions and configured review grants are checked for review requests.

An agent explicitly set to **Anyone who can comment on the PR** permits direct invocation and finding replies by external human commenters without workspace membership. Repository review grants still apply; groups keep their caller restrictions. This exposes paid review invocation, not the dashboard or management MCP. People restrictions do not block access to these open agents; change the agent's trigger policy or repository grant to disable it.

## Untrusted code and data

PR contents, build scripts, skills, and tool responses can contain malicious instructions. Review agents execute repository code with terminal access in disposable Modal sandboxes that have outbound internet access. Sandboxing does not make secrets given to that code safe: only provide disposable test credentials in repository environments, restrict MCP tools to the access the reviewer needs, and treat review output as untrusted. Long-lived model and MCP credentials stay in the API service; workers use active-job-scoped relay tokens.

Source code is sent to the configured model provider and executed on Modal. Selected MCP services receive tool calls. Optional trace content exports can include source code, tool arguments, tool results, and secrets embedded in text; those values are not automatically redacted. Keep tracing off or content capture disabled unless the collector is suitable for that data.

Repositories with a setup command also run that command against their trusted default branch in a separate warming sandbox. No service, job, GitHub, model, MCP, or configured repository test credentials are supplied to that sandbox. Its filesystem snapshot can retain default-branch source, dependencies, and compiled extensions for seven days on Modal. PR sandboxes are never promoted into reusable snapshots. Each review replaces the source with its exact head revision and reruns setup; dependency directories, caches, and generated native extensions can be reused. See [dependency reuse](docs/OPERATIONS.md#reuse-repository-dependencies) for required build-input declarations, compatibility, and fallback behavior.

## Resource use and operations

Run time and model-call limits are optional per reviewer. `CONCURRENCY=0` leaves concurrency to upstream quotas. Configure these controls and provider/Modal budgets for your deployment. Uploads, review data, logs, and screenshots have no application size or retention cap; monitor database growth and service memory. Operate one API worker and one service instance. See [deployment and recovery](docs/DEPLOYMENT.md) for backups and updates.
