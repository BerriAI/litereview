# Validation

LiteReview separates repeatable local tests from opt-in live checks. A passing build or fixture does not establish compatibility with every model, MCP server, repository, or hosting account.

## Automated checks

Run the commands in [CONTRIBUTING.md](../CONTRIBUTING.md). The suite covers authorization, SSO and linked GitHub identities, repository permissions, setup recovery, model protocols, MCP OAuth, review publication, runtime selection, timing and optional budgets, tracing, and large-content handling. CI runs with SQLite and PostgreSQL 16, checks the frontend, and builds the Docker image. The frontend build includes the MIT license and complete production browser dependency notices.

The October 10, 2026 pre-release baseline at commit `7a6238b` passed **273 tests with 1 skipped** using SQLite, plus Ruff and the TypeScript/Vite production build. The skipped check requires PostgreSQL to enforce a legacy column length. Gitleaks found no secrets in the 67 commits reachable through locally available refs at that point. These checks are bounded evidence, not a complete security audit.

## Selectable harness validation

The September 2026 integration matrix exercised the pinned versions below in independent Modal sandboxes using compatible gateway models. Each harness completed a sample skill check, terminal command, internet request, browser and desktop interaction, configured MCP call, and a seeded defect review.

| Harness | Pinned runtime |
| --- | --- |
| Built-in | 1 |
| Pi | 0.87.1 |
| OpenCode V1 | 1.18.33 |
| OpenCode V2 | 2.0.18 |
| Codex | 0.158.0 |
| Claude Code Agent SDK | 0.3.284 |
| Deep Agents | 0.7.19 |

The authoritative pins are in `runner/catalog.py`. Prior live checks also exercised signed GitHub webhooks, installation-authenticated source downloads, App-authored results, duplicate-delivery handling, and simultaneous sandboxes in an isolated test deployment. Direct-provider account authentication was not tested in that matrix; adapter behavior is covered by protocol fixtures. Historical evidence is not a claim that your new deployment has completed a review.

## Check your own deployment

Follow [Get your first review](DEPLOYMENT.md#get-your-first-review): verify sign-in and repository access, run **Save and check reviewer**, and then request a real review on a PR you control. Confirm the response is attached to the intended PR and commit and inspect any limitations. A model listing, healthy API, or saved App alone does not establish a working review path.

For opt-in runtime checks:

```sh
uv run python -m scripts.modal_smoke
uv run python -m scripts.modal_app_smoke --repo YOUR-OWNER/YOUR-LITEREVIEW-FORK --pr YOUR-PR-NUMBER
```

The second command builds and runs the selected LiteReview PR's code inside Modal; use only a trusted PR in your own fork. Read the script's requirements before running it. These checks require Modal credentials; the app smoke also needs authenticated `gh` and gateway/model credentials. They consume credits. Store evidence under ignored `.test-artifacts/` and redact private code, credentials, and account identifiers before sharing it.

`scripts/ui_acceptance.py` is a legacy opt-in UI check requiring pre-existing local fixtures and access-key mode. It is not part of the fresh-install procedure or CI. New onboarding changes should also be exercised with an isolated database in a browser, including failed setup, retry, refresh, and narrow layouts.
