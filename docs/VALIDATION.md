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
| Multiple model types | [A mixed-model run](https://github.com/BerriAI/litereview/pull/1#issuecomment-5880148618) ran Claude Haiku 4.5 and GPT-4o mini concurrently in separate sandboxes; GPT-4o mini also completed the earlier clean review |
| Sandbox cleanup | All eight sandboxes from the initial seven-agent + desktop runs exited with code 0; a separate real 10-second hard-timeout test raised the expected Modal timeout, and cleanup/status handling is also covered by tests |

The visual fixture draws its button text on canvas. The browser's accessibility tree still contains the button name when its text becomes invisible; the screenshots establish the rendered defect. Browser and desktop evidence showed the confirmation state after interacting.

The mixed-model run also exposed a review-quality difference: Haiku returned no findings on the comment-only change, while GPT-4o mini reported a non-actionable comment-clarity nit. V1 intentionally publishes independent agent outputs; it has no judge/filter that guarantees finding quality. This is useful evidence when choosing models and instructions, rather than a claim that every finding is correct.

The PR originally contained two intentional defects at `914a25c0af107b86a555edaa51e5f83800752e08`. Its head was subsequently restored to correct behavior with a comment-only change for the clean-result test. Old review comments retain their original commit references.

## Full application and internet check

A fresh real Modal sandbox also built and ran LiteReview itself from PR #1 at `00fb8ded67b6f8fc57f16e1f92187b3f943d9e0e`. This check uses the production sandbox image, archive extractor, and tool implementation; it drives the tools deterministically, then sends the resulting desktop screenshot to the real gateway model. The autonomous agent/webhook path was tested separately above.

- All 37 extracted source files matched SHA-256 hashes of the private GitHub archive fetched on the host. GitHub credentials stayed outside the sandbox.
- Outbound HTTPS inside the sandbox returned HTTP 200 from GitHub's API, npm, PyPI, and example.com. Chromium separately loaded the public Python documentation and returned a screenshot.
- The configured setup command installed Python dependencies with `uv sync --frozen --no-dev`, installed Node dependencies with `npm ci --prefix frontend`, and built the frontend. The configured start command launched the FastAPI server. Setup plus readiness completed in 21.1 seconds in this run; that is an observation, not a startup guarantee.
- Browser tools signed into the disposable app, opened the agent editor, and navigated to agent groups. Desktop keyboard tools navigated back to the app and returned a full 1280×900 screenshot. No page JavaScript errors were observed.
- Claude Haiku 4.5 through the real gateway correctly transcribed “LiteReview”, “PR agents”, and “Create agent” from the desktop screenshot. The disposable app used test-only local credentials, with dispatch disabled.
- The sandbox was terminated after the check. The repeatable command is `uv run python -m scripts.modal_app_smoke --repo BerriAI/litereview --pr 1`; it executes the selected PR and consumes Modal/model credits. JSON evidence and PNGs are saved locally in ignored `.test-artifacts/app-smoke-*` files.

This confirms the tested PR can install dependencies, build, run, access the internet, and be inspected visually. It is not a guarantee that arbitrary repositories boot without configuration. Setup/start commands, service dependencies, private registry access, and test accounts remain repository-specific. The current checkout is a source archive: no `.git` history, submodule/LFS materialization, or symlink preservation.

## Automated checks

- 26 tests cover signature/authentication checks, access policy, duplicate webhook deliveries, independent group expansion, immutable configuration, credential redaction, job-token expiry and budgets, MCP access enforcement, archive traversal, screenshot access, cleanup, rejected commands, result formatting, and browser-bound GitHub App registration callbacks.
- Direct OpenAI and Anthropic adapters are tested against local protocol fixtures. These tests verify image payloads and tool result translation without real direct-provider credentials.
- CI passed against SQLite and Postgres, built the React/TypeScript UI, and built the production Docker image. The final commit is checked by the same workflow.
- Live Playwright UI checks create and edit agents, reuse an agent in two groups, set instance counts, attach skills, and select computer use. Desktop and 768/390/320 px widths were inspected; no JavaScript errors or page-level horizontal overflow remained.
- A separate Modal smoke test verifies browser clicking, desktop keyboard input, and actual screenshot payloads.

## UI overhaul

The UI was rebuilt using the linked internal brand skill and the LiteLLM dashboard, Learn, and ROI Calculator references; see [UI.md](UI.md) for the sources and component rules. This includes the official train/provider assets, navigation and tables, capability filters, full-page resource editors, group membership links and command preview, and review evidence/source links.

After the overhaul, the production frontend build and all 25 backend tests passed. The expanded live browser test passed agent creation/editing/reuse, persisted group instance counts, skills and computer-use selection, capability filtering, every resource editor, mobile navigation with keyboard focus and Escape, run attribution/results, and 768/390/320-pixel page widths without document overflow. Desktop pages, full-page editors, and mobile navigation/forms were also captured and visually inspected. The user requested a light-only interface; automatic dark mode was removed, and the browser check verifies that a dark system preference still renders the white UI. No page JavaScript errors were observed. These UI checks did not call models, launch Modal jobs, or change the production integration setup.

## External setup that remains

The production GitHub App registration/installation and GitHub OAuth sign-in require the deployment's own App credentials. Live GitHub testing used a temporary repository webhook and an explicitly development-only GitHub token. The guided App setup and App permissions are implemented, but no production GitHub App has been registered in BerriAI by this work. A private App for BerriAI repositories must be owned by BerriAI; an organization owner may need to create/install it.

The application is deployed to Render at [litereview.onrender.com](https://litereview.onrender.com), backed by private Postgres. The original temporary repository webhook and test-only MCP/error/clean-review configurations were removed after development validation. Those review histories and screenshots remain in the local database; they were not copied into production.

## Render deployment verification

On 28 September 2026, the Docker service deployed in the Litellm Render workspace, Oregon, with one Starter instance and a separate PostgreSQL 16 database. External database access has an empty IP allowlist. `PUBLIC_URL` resolves to Render's HTTPS address; automatic deployments wait for CI to pass. Production credentials were configured privately, including the user's replacement gateway key.

- Public `/health` returned 200 and queried Postgres. Administrator sign-in succeeded through both the API and the live browser UI, using secure HttpOnly session cookies.
- Configuration, runs, screenshots, and sandbox bootstrap endpoints rejected unauthenticated requests with 401. Cross-origin mutations returned 403. An unsigned webhook returned 401.
- The hosted gateway connection discovered 1,230 model IDs. A temporary agent tested Claude Haiku 4.5 image input and tool calling through the deployed API successfully, then was removed. No sample agents or review history were imported into the production database.
- A fresh sandbox using the credentials configured on Render (`sb-H1NhNrxsGXcvUPZLp4tfyJ`) reached the live health endpoint, confirmed the API still required authentication, and received HTTP 200 from GitHub, PyPI, and npm. Its browser loaded the deployed sign-in page and returned a PNG; its desktop tool returned a full-screen PNG. The sandbox was terminated after verification. This was a deterministic connectivity/tool check, not a production webhook-triggered review.
- The live signed-in interface was visually inspected and remains light. The model connection's key is not returned by the resource API.
- Real App registration exposed a rejected `redirect_url` containing a query string. Commit `aa22910` moved `state` to GitHub's registration URL, bound the callback to an expiring browser cookie, and cleared the cookie after conversion. All 26 tests, Postgres integration checks, frontend build, and Docker build passed CI; Render deployed that fix. GitHub then accepted the manifest.
- GitHub explicitly reported: “You don't have permission to create apps for BerriAI.” No personal App was created as a fallback. **Production PR triggers and GitHub OAuth sign-in are not enabled or verified yet.** A BerriAI organization owner must complete App registration/installation; see [DEPLOYMENT.md](DEPLOYMENT.md).

No direct OpenAI or Anthropic account credentials were provided, so live direct-provider account verification remains outside this run. Gateway calls and protocol-level adapter tests passed.


## Private cross-repository acceptance validation

Separate private snapshots of `BerriAI/litellm-roi-calculator` and `pallets/flask` were reviewed through an actual private GitHub App installation. The test service had its own database and Modal app; it used no development GitHub token. No test data or configuration was copied into the deployed workspace.

- Actual GitHub comment → signed webhook → installation-authenticated source download → independent Modal sandbox → gateway → App-authored PR comment worked on both repositories.
- Each repository ran five reviewers: code only, terminal, browser, desktop computer use, and MCP with an attached skill/supporting file. Following the runtime fixes, all ten completed their configured checks without limitations and found the seeded navigation or authorization regression.
- The source applications were installed and started inside the sandboxes: the ROI app with sample data, and Flask's tutorial blog with disposable local users. Browser and desktop reviewers inspected screenshots and interacted with those running apps. Terminal reviewers ran tests/reproductions and verified outbound HTTPS. Exact supporting skill paths and real Streamable HTTP MCP calls were recorded.
- Ten simultaneous comment requests were accepted alongside two larger reviews. Twenty real sandboxes overlapped, with distinct sandbox IDs. The optional workspace cap is also covered by automated tests.
- Claude Haiku 4.5 and GPT-4o mini were exercised via the gateway. The no-findings publication path was exercised. Clean documentation changes also produced false-positive findings: review criteria and model choice remain user-controlled, and infrastructure validation is not a guarantee of review quality.
- GitHub OAuth sign-in completed as the allowed administrator. Private API/run/screenshot endpoints returned 401 without authentication. Redelivering a real GitHub webhook did not create a duplicate review. All 40 validation sandboxes exited.
- Invalid completion payloads now produce a recoverable 422 response; the agent can correct its response instead of receiving an opaque server error. Supporting skill files have explicit paths. Browser tool guidance matches the accessibility tree and ambiguous selectors no longer silently choose the first element.

Private evidence, model tool-call audits, screenshots, source SHAs, and run IDs remain under the ignored local `.test-artifacts/private-validation-20260928/` directory. Automated validation now includes 31 tests, plus a successful frontend build. Direct-provider adapters are covered by protocol fixtures; live calls in this test used the supplied gateway credentials.
