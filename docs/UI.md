# LiteReview UI

The interface follows the LiteLLM family of products rather than introducing a separate visual identity.

## References

- [LiteLLM brand visuals, internal-skills PR #86](https://github.com/BerriAI/litellm-internal-skills/pull/86), read at commit `1fea7d4dc630f011fd9a3282c657ca4c39bd1366`: `SKILL.md`, `references/brand.md`, `references/quality-bar.md`, and `assets/brand.css`.
- LiteLLM dashboard: shared sidebar and page-header patterns under `ui/litellm-dashboard/src/components/shared/`.
- [LiteLLM ROI Calculator](https://github.com/BerriAI/litellm-roi-calculator): compact navigation, neutral data tables, and settings sections.
- LiteLLM Learn: the local gateway-course website source that produces the `BerriAI/litellm-learn` site, including its navigation, content hierarchy, and mobile treatment.

The train, favicon, and OpenAI/Anthropic/GitHub SVGs in `frontend/public/brand/` are unchanged assets from PR #86. Geist and Geist Mono are served locally through the existing font packages. Generic gateways use a generic network icon; they are not assumed to be LiteLLM gateways.

## Layout and behavior

- PR agents, groups, and runs sit together under Review. MCP servers, Skills, and Models have direct entries under Resources. Repositories, People, and Settings sit under Workspace. Settings contains advanced GitHub App recovery.
- Agent tables show the model connection, enabled capabilities, and clickable group memberships. Filters separate code-only, browser, and computer agents; search includes model IDs.
- Agents, groups, skills, and repository environments have full-page editors. Their labeled sections share one content grid and a persistent save bar. Resource editors keep sidebar navigation available and make the covered page inert. Agent drafts survive resource navigation and OAuth redirects in the same tab. Compact and guided setup dialogs retain modal focus containment.
- Group editing shows the saved agent identity, instance count, and a Copy PR comment action. With SSO enabled, People derives membership from Google sign-ins and verified GitHub links, with optional access restrictions; each repository exposes Review permissions with All or specific agent/group selections. Keep permission controls out of agent/group editors.
- Review results show attribution, findings, evidence, links to the reviewed source, and screenshots. Model-call counts and sandbox identifiers live in expandable execution details.
- Mobile navigation opens a drawer, supports Escape, and makes the underlying page inert. Tables scroll within their own region; editors use a single column. The interface is light-only, regardless of the system appearance setting. Do not add dark mode without an explicit request.

Use flat surfaces, hairline separators, restrained 400/500 type weights, and violet for primary actions and selections. Status colors convey outcomes. Avoid decorative metrics, gradients, icon tiles, and badges around every label. Use actual API data and retain the distinction between no findings and an incomplete review.

## Onboarding

All approved Google members have the same workspace interface and management access. The home page routes unlinked accounts to `/start`, where they can link GitHub or open the workspace; direct resource links remain usable before linking. Linked members open the workspace. GitHub controls App creation and installation permissions. Repositories has one next action based on verified state. Sync must preserve exclusions and grants. A private harness check establishes model/runtime readiness; a real PR review establishes the first useful result. Expired sessions lead to Google sign-in with a validated return path. Never show sample reviewer names as usable production commands.

## Validation

`uv run python -m scripts.ui_acceptance` checks reusable agent/group editing, saved instance counts, skills, capability filters, every editor, mobile navigation and keyboard behavior, run results, and 768/390/320-pixel layouts. It requires the local live-test workspace, including its named model connection, skill, repository, and review history. It does not launch agents or call models. Screenshots remain in ignored `.test-artifacts/` files.
