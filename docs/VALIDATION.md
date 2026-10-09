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

## Selectable harness validation

Validated privately on 28 September 2026 in a separate database and Modal app. These checks did not create public resources, post GitHub comments, or change production settings.

| Harness | Pinned runtime | Code only + sample skill | Terminal, internet, browser, desktop, shared MCP | Defect with file/line |
| --- | --- | --- | --- | --- |
| Built-in | 1 | Passed | Passed | Passed |
| Pi | 0.87.1 (`@earendil-works/pi-coding-agent`) | Passed | Passed | Passed |
| OpenCode V1 | 1.18.33 (`opencode-ai`) | Passed | Passed | Passed |
| OpenCode V2 | 2.0.18 (`@opencode/cli`) | Passed | Passed | Passed |
| Codex | 0.158.0 (`@openai/codex`) | Passed | Passed | Passed |
| Claude Code | Agent SDK 0.3.284 | Passed | Passed | Passed |
| Deep Agents | 0.7.19 | Passed | Passed | Passed |

Each harness ran its real SDK/CLI in its own Modal sandbox. The full check executed a Python assertion, fetched example.com over HTTPS (200), operated a running sample app using browser tools, then used desktop mouse/keyboard actions. Saved screenshots show the red swatch and `Saved: desktop-ok`; browser actions separately produced `Saved: browser-ok`. Each returned the random word from an attached skill file and its harness-specific proof from the same authenticated remote MCP connection. All completed with empty findings and limitations on the correct fixture. A separate arithmetic regression produced one finding at `maths.py:2` from every harness.

Live image inventories confirmed that only the selected harness package was installed in each sandbox. Native model-request tool lists confirmed code-only runs had no terminal, native editing, or delegation tools. Unit tests also cover image selection, protocol headers, credential separation, model/call restrictions, screenshot content blocks, capability gating, cancellation, and checks never publishing to GitHub. The browser UI passed harness/model searching, persisted selection, a real Test agent run, and stopping a running test. Search now happens directly in the selected field; filtering, arrow/Enter selection, Escape preserving the old value, and custom model IDs were checked in the browser. The local suite passed 66 tests and the frontend production build.

Initial code-only checks used GPT-4.1 (GPT-5.4 for Codex) and Claude Sonnet 4.6 through the supplied gateway. During the full matrix the OpenAI provider returned 429 with no credits remaining. The accepted full and defect matrices used Claude Sonnet 4.6: Chat Completions for Built-in/Pi/OpenCode/Deep Agents, Messages for Claude Code, and the gateway's Responses translation for Codex. Interrupted and quota-failed attempts are retained as failed evidence, not counted as passes. Pi, both OpenCode versions, and Deep Agents also passed the full matrix using the Anthropic Messages adapter against the gateway; that additional check caught and fixed Pi adding `/v1` twice. No live direct-provider account credentials were available; direct account authentication remains untested, while endpoint/header behavior is covered with protocol fixtures.

Evidence is retained privately in the ignored `.test-artifacts/harness-validation/` directory: accepted run IDs, statuses, tool audits, package inventories, MCP audit, and screenshots. These are bounded capability and review-contract checks, not a guarantee for every model, MCP server, or repository. The existing GitHub/App integration checks above cover the shared source-download/publication path; this harness matrix used disposable source fixtures.


## Google SSO and minimum review durations

Validated October 8, 2026:

- 104 automated tests pass with SQLite and PostgreSQL 16. Coverage includes verified Google JWT signatures/claims, PKCE, browser-bound single-use OAuth state, disabled legacy authentication, member/admin access, policy/session expiry, stable GitHub account linking, and signed webhook authorization.
- Timing tests cover per-agent/group parsing, invalid/conflicting durations, preserved skills/tool snapshots, monotonic finish gating, independent server-side completion checks, per-job timeout propagation, failure reporting, and Codex continuation in the same thread after an early exit.
- A real Codex 0.158.0 Modal sandbox using `openai/gpt-5.4` through the configured gateway completed a 60-second minimum review after 64.3 seconds. It read the attached supporting skill file, ran the repository's terminal check and additional checks, then submitted a successful structured result. The run used 30 model calls. Evidence is in the local ignored `.test-artifacts/timed-review/result.json`; no GitHub comment was posted and production settings were unchanged. Other harness timing paths have not received new live model checks in this release; their prior capability matrix is above.
- The frontend production build and Docker build pass. Local browser checks cover Google-only sign-in, member account instructions, administrator workspace access, the SSO account navigation, and rendered command documentation. The account page fits a 390-pixel viewport without horizontal overflow. Test identities were local fixtures, not real Google sign-ins.
- Production is reachable, and its gateway model discovery succeeds. It reports a configured GitHub App, no connected repositories, and no saved agents. Google SSO activation is still blocked: the existing Render/Moyai OAuth client rejects LiteReview's callback with `redirect_uri_mismatch`, and the current Google account lacks access to edit its project. This is not a completed live Google SSO rollout. Follow [the callback registration and rollout steps](DEPLOYMENT.md#google-sso-rollout).

## Streamlined onboarding

Validated October 9, 2026:

- All 153 backend tests pass on SQLite and PostgreSQL 16. The new cases cover idempotent installation sync, saved environments/secrets, restricted and removed permissions, explicit repository exclusions/restoration, setup-reset races, administrator-only mutations, current GitHub repository permissions and stable identity IDs, private-check readiness, unavailable services, and safe OAuth return destinations.
- The production frontend and Docker builds pass, as does Ruff. GitHub CI runs the same database, frontend, and image checks before deployment.
- The actual app was exercised locally in an isolated database through browser controls: repository sync, starter reviewer creation/model discovery, private-check retry without duplicate agents, first-review command, member-only navigation, unlinked GitHub state, repository permission editor, Settings, reset/refresh/undo, expired-session Google recovery, and keyboard navigation. Owner/member/docs pages fit 320-pixel viewports without page overflow. These checks use fixture GitHub/model/Modal responses; they establish UI behavior, not live review execution. Local screenshots are under ignored `.test-artifacts/onboarding-live/`.
- The published docs and in-app guide now match Repositories, People, and Settings, including owner handoff, automatic repository synchronization, runtime prerequisites, and recovery.
- Google SSO is active in production: the callback was registered, an actual sign-in succeeded, the domain is restricted to `berri.ai`, and legacy token login is disabled. This supersedes the October 8 callback blocker above. The existing personal private GitHub App cannot be installed in BerriAI; an organization owner still needs to finish the organization-owned App setup. No new production PR review is claimed by this onboarding validation.

Minimum review time is now saved in the reviewer UI. Timing tests verify that plain name-only comments apply independent saved times to direct agents and every group instance, accepted jobs keep their original time after edits, old reviewers remain untimed, and comment overrides and invalid UI/API values are rejected. The actual local editor saved a five-minute value, retained it after reload, and the member page still generated `@litereview reviewer`. The timing enforcement and previously verified Codex continuation path are unchanged; no new live model call was needed for this settings change.

## Optional agent limits (October 9, 2026)

- No LiteReview run timeout or model-call budget is set by default, including for agents with a saved minimum. The agent editor can save or clear each optional limit independently; direct and group calls snapshot the same settings.
- Regression tests cover passing the previous 60-call/400-message cutoffs, active-job authorization after ten hours, explicit limits, immutable snapshots, validation, Modal lifetime selection, and observing an existing sandbox after API restart. API shutdown leaves a registered running sandbox alive.
- The actual local app was checked in the browser: both fields start blank, explicit values survive a reload, a maximum below the minimum is rejected, and clearing values persists without changing the minimum.
- The pinned Deep Agents dependency was exercised with a real LangGraph that completed 200 steps using an unbounded recursion setting. This does not validate model compaction or multi-day agent runs. Provider/network errors can still stop a run.
- Modal requires a lifetime of at most 24 hours. LiteReview uses the full platform lifetime when no maximum is selected, and reports that constraint in settings and incomplete results. Sandbox replacement/resumption is not implemented.
