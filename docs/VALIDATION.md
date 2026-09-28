# V1 validation

Validated on 28 September 2026 using the private repository, a real LiteLLM gateway, and real Modal sandboxes. The test PR is a draft and should not be merged.

## Live checks

| Check | Evidence |
| --- | --- |
| GitHub comment → webhook → seven parallel jobs → one attributed result comment | [Seven-agent review](https://github.com/BerriAI/litereview/pull/1#issuecomment-5879827650); seven distinct sandbox IDs persisted |
| Browser visual review | The reviewer opened the sample app, received rendered PNG image inputs, observed invisible canvas text, clicked the canvas, and inspected the confirmation screenshot |
| Computer use | A separate desktop-only reviewer navigated Chromium with keyboard/mouse input, inspected full-screen PNGs, and clicked through the same app |
| MCP and skill attachments | [MCP + desktop result](https://github.com/BerriAI/litereview/pull/1#issuecomment-5879889220); real Streamable HTTP server exposed two tools, only the selected review-criteria tool was available to the agent; attached SKILL.md and a supporting file were read |
| Changed PR head during a run | The MCP/desktop comment explicitly warns that newer commits exist, while retaining the original reviewed SHA |
| Clean result | [No findings](https://github.com/BerriAI/litereview/pull/1#issuecomment-5880008939) on a comment-only change, with an empty limitations list |
| Failure result | [Expected invalid-model failure](https://github.com/BerriAI/litereview/pull/1#issuecomment-5879912474), explicitly incomplete rather than no findings |
| Invalid command | [Review not started](https://github.com/BerriAI/litereview/pull/1#issuecomment-5880009727); no agent jobs created |
| Multiple model types | Real gateway requests used Claude Haiku 4.5 and GPT-4o mini; the latter completed the clean review |
| Sandbox cleanup | All eight sandboxes from the initial seven-agent + desktop runs exited with code 0; cleanup logic is also covered by a test |

The visual fixture draws its button text on canvas. The browser's accessibility tree still contains the button name when its text becomes invisible; the screenshots establish the rendered defect. Browser and desktop evidence showed the confirmation state after interacting.

The PR originally contained two intentional defects at `914a25c0af107b86a555edaa51e5f83800752e08`. Its head was subsequently restored to correct behavior with a comment-only change for the clean-result test. Old review comments retain their original commit references.

## Automated checks

- 24 tests cover signature/authentication checks, access policy, duplicate webhook deliveries, independent group expansion, immutable configuration, credential redaction, job-token expiry and budgets, MCP access enforcement, archive traversal, screenshot access, cleanup, rejected commands, and result formatting.
- Direct OpenAI and Anthropic adapters are tested against local protocol fixtures. These tests verify image payloads and tool result translation without real direct-provider credentials.
- The CI workflow runs the suite against SQLite and Postgres, builds the React/TypeScript UI, and builds the production Docker image.
- Live Playwright UI checks create and edit agents, reuse an agent in two groups, set instance counts, attach skills, and select computer use. Desktop, dark appearance, and 768/390/320 px widths were inspected; no JavaScript errors or page-level horizontal overflow remained.
- A separate Modal smoke test verifies browser clicking, desktop keyboard input, and actual screenshot payloads.

## External setup that remains

The production GitHub App registration/installation and GitHub OAuth sign-in require the deployment's own App credentials. Live GitHub testing used a temporary repository webhook and an explicitly development-only GitHub token. The guided App setup and App permissions are implemented, but no production GitHub App has been registered in BerriAI by this work. A private App for BerriAI repositories must be owned by BerriAI; an organization owner may need to create/install it.

The application has not been deployed to Render. The checked-in Blueprint and Docker image are the deployment path. Modal and the real gateway were exercised from a temporary HTTPS development endpoint. Temporary test webhook/servers should be removed after validation; persistent settings belong in the Render deployment.

No direct OpenAI or Anthropic account credentials were provided, so live direct-provider account verification remains outside this run. Gateway calls and protocol-level adapter tests passed.
