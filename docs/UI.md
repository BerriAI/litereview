# LiteReview UI

The interface follows the LiteLLM family of products rather than introducing a separate visual identity.

## References

- [LiteLLM brand visuals, internal-skills PR #86](https://github.com/BerriAI/litellm-internal-skills/pull/86), read at commit `1fea7d4dc630f011fd9a3282c657ca4c39bd1366`: `SKILL.md`, `references/brand.md`, `references/quality-bar.md`, and `assets/brand.css`.
- LiteLLM dashboard: shared sidebar and page-header patterns under `ui/litellm-dashboard/src/components/shared/`.
- [LiteLLM ROI Calculator](https://github.com/BerriAI/litellm-roi-calculator): compact navigation, neutral data tables, and settings sections.
- LiteLLM Learn: the local gateway-course website source that produces the `BerriAI/litellm-learn` site, including its navigation, content hierarchy, and mobile treatment.

The train, favicon, and OpenAI/Anthropic/GitHub SVGs in `frontend/public/brand/` are unchanged assets from PR #86. Geist and Geist Mono are served locally through the existing font packages. Generic gateways use a generic network icon; they are not assumed to be LiteLLM gateways.

## Layout and behavior

- PR agents, groups, and runs sit together under Review. Shared connections and skills sit under Workspace.
- Agent tables show the model connection, enabled capabilities, and clickable group memberships. Filters separate code-only, browser, and computer agents; search includes model IDs.
- Agents, groups, skills, and repository environments have full-page editors. Their labeled sections share one content grid and a persistent save bar. Native dialogs provide focus containment, Escape behavior, and focus restoration.
- Group editing shows the saved agent identity, instance count, repository/caller access, and the resulting GitHub command.
- Review results show attribution, findings, evidence, links to the reviewed source, and screenshots. Model-call counts and sandbox identifiers live in expandable execution details.
- Mobile navigation opens a drawer, supports Escape, and makes the underlying page inert. Tables scroll within their own region; editors use a single column. The interface is light-only, regardless of the system appearance setting. Do not add dark mode without an explicit request.

Use flat surfaces, hairline separators, restrained 400/500 type weights, and violet for primary actions and selections. Status colors convey outcomes. Avoid decorative metrics, gradients, icon tiles, and badges around every label. Use actual API data and retain the distinction between no findings and an incomplete review.

## Validation

`uv run python -m scripts.ui_acceptance` checks reusable agent/group editing, saved instance counts, skills, capability filters, every editor, mobile navigation and keyboard behavior, run results, and 768/390/320-pixel layouts. It requires the local live-test workspace, including its named model connection, skill, repository, and review history. It does not launch agents or call models. Screenshots remain in ignored `.test-artifacts/` files.
