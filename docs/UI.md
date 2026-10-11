# LiteReview UI

The interface follows the LiteLLM family of products rather than introducing a separate visual identity.

## Visual identity

Use the [LiteLLM dashboard](https://github.com/BerriAI/litellm/tree/main/ui/litellm-dashboard) as a reference for compact navigation, neutral data tables, and settings sections. This guide and the existing frontend components contain the conventions needed to contribute; no private design repository is required.

Use Geist/Geist Mono typography, ink `#272B37`, restrained violet `#5B3FD1`, neutral surfaces, and thin borders. The train and favicon in `frontend/public/brand/` are LiteLLM project branding. OpenAI/Anthropic/GitHub SVGs identify their integrations. See [branding and licenses](../THIRD_PARTY_NOTICES.md). Geist and Geist Mono are served locally with their license texts included in the production build. Generic gateways use a generic network icon; they are not assumed to be LiteLLM gateways.

## Layout and behavior

- PR agents, groups, and runs sit together under Review. MCP servers, Skills, and Models have direct entries under Resources. Repositories, People, and Settings sit under Workspace. Settings contains advanced GitHub App recovery.
- Agent tables show the model connection, enabled capabilities, and clickable group memberships. Filters separate code-only, browser, and computer agents; search includes model IDs.
- PR agents sorts by Times used, descending, with stable name ordering for ties. Count PR runs once per agent, including group use; exclude private checks and fusion branches.
- Repository editors put Automatic reviews before the optional environment settings. Rules select a reviewer, an optional list of PR authors, and GitHub PR events. Show the recurring cost and publication behavior before Save, distinguish missing App event subscriptions from saved rules, and keep disabled rules editable.
- Agents, groups, skills, and repository environments have full-page editors. Their labeled sections share one content grid and a persistent save bar. Resource editors keep sidebar navigation available and make the covered page inert. Agent drafts survive resource navigation and OAuth redirects in the same tab. Compact and guided setup dialogs retain modal focus containment.
- Group editing shows the saved agent identity, instance count, and a Copy PR comment action. With SSO enabled, People derives membership from Google sign-ins and verified GitHub links, with optional access restrictions; each repository exposes Review permissions with All or specific agent/group selections. Keep permission controls out of agent/group editors.
- Review results show attribution, findings, evidence, links to the reviewed source, and screenshots. Model-call counts and sandbox identifiers live in expandable execution details.
- Mobile navigation opens a drawer, supports Escape, and makes the underlying page inert. Tables scroll within their own region; editors use a single column. The interface is light-only, regardless of the system appearance setting. Do not add dark mode without an explicit request.

Use flat surfaces, hairline separators, restrained 400/500 type weights, and violet for primary actions and selections. Status colors convey outcomes. Avoid decorative metrics, gradients, icon tiles, and badges around every label. Use actual API data and retain the distinction between no findings and an incomplete review.

## Onboarding

All approved Google members have workspace management access. Only admins see Spend navigation and model costs on run lists, run summaries, and agent results. A member opening `/spend` sees an access restriction without fetching spend data. The server determines admin status from the deployment configuration. The home page routes unlinked accounts to `/start`, where they can link GitHub or open the workspace; direct resource links remain usable before linking. Linked members open the workspace. GitHub controls App creation and installation permissions. Repositories has one next action based on verified state. Sync must preserve exclusions and grants. A private harness check establishes model/runtime readiness; a real PR review establishes the first useful result. Expired sessions lead to Google sign-in with a validated return path. Never show sample reviewer names as usable production commands.

## Validation

`uv run python -m scripts.ui_acceptance` checks reusable agent/group editing, saved instance counts, skills, capability filters, every editor, mobile navigation and keyboard behavior, run results, and 768/390/320-pixel layouts. It requires the local live-test workspace, including its named model connection, skill, repository, and review history. It does not launch agents or call models. Screenshots remain in ignored `.test-artifacts/` files.
