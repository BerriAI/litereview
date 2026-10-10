# Validation

LiteReview separates repeatable local tests from opt-in live checks. A passing build or fixture does not establish compatibility with every model, MCP server, repository, or hosting account.

## Automated checks

Run the commands in [CONTRIBUTING.md](../CONTRIBUTING.md). The suite covers authorization, SSO and linked GitHub identities, repository permissions, setup recovery, model protocols, MCP OAuth, review publication, runtime selection, timing and optional budgets, tracing, and large-content handling. CI runs with SQLite and PostgreSQL 16, checks the frontend, and builds the Docker image. The frontend build includes the MIT license and complete production browser dependency notices.

The October 10, 2026 pre-release baseline at commit `7a6238b` passed **273 tests with 1 skipped** using SQLite, plus Ruff and the TypeScript/Vite production build. The skipped check requires PostgreSQL to enforce a legacy column length. Gitleaks found no secrets in the 67 commits reachable through locally available refs at that point. These checks are bounded evidence, not a complete security audit.

## Codex runtime validation

LiteReview runs Codex 0.158.0, pinned in `runner/catalog.py`. Automated tests exercise large Unicode review contexts, process startup, instruction preservation, same-session continuation, saved-reviewer migration, and incompatible connection recovery. The full review context stays on disk so neither operating-system argument limits nor Codex's initial input limit truncate a PR.

The September 2026 live checks exercised this Codex version in Modal with a compatible gateway model, including sample skills, terminal commands, internet access, browser and desktop interaction, a configured MCP call, and a seeded defect review. Prior integration checks also covered signed GitHub webhooks, installation-authenticated source downloads, App-authored results, duplicate-delivery handling, and simultaneous sandboxes. Historical evidence is not a claim that a new deployment has completed a review.

CI also installs the pinned Codex package and runs `scripts.codex_smoke` against a local model fixture. This checks a complete 2.7 MB Unicode context file, native terminal execution, instruction preservation, structured MCP submission, and the same session and cache key after a timed continuation. It uses no provider credentials or paid inference and does not measure actual cache hits. To repeat it from the repository root:

```sh
codex_test_dir="$(mktemp -d)"
codex_version="$(uv run python -c 'from runner.catalog import HARNESS_VERSIONS; print(HARNESS_VERSIONS["codex"])')"
npm install --prefix "$codex_test_dir" --omit=dev --no-package-lock "@openai/codex@$codex_version"
uv run python -m scripts.codex_smoke --codex "$codex_test_dir/node_modules/.bin/codex"
```

The command succeeds when its JSON output reports `structured_review_completed`, `same_thread`, and `same_prompt_cache_key` as `true`. A fixture failure is a runtime compatibility issue; it does not test a production provider's credentials or model quality.

## Check your own deployment

Follow [Get your first review](DEPLOYMENT.md#get-your-first-review): verify sign-in and repository access, run **Save and check reviewer**, and then request a real review on a PR you control. Confirm the response is attached to the intended PR and commit and inspect any limitations. A model listing, healthy API, or saved App alone does not establish a working review path.

For opt-in runtime checks:

```sh
uv run python -m scripts.modal_smoke
uv run python -m scripts.modal_app_smoke --repo YOUR-OWNER/YOUR-LITEREVIEW-FORK --pr YOUR-PR-NUMBER
```

The second command builds and runs the selected LiteReview PR's code inside Modal; use only a trusted PR in your own fork. Read the script's requirements before running it. These checks require Modal credentials; the app smoke also needs authenticated `gh` and gateway/model credentials. They consume credits. Store evidence under ignored `.test-artifacts/` and redact private code, credentials, and account identifiers before sharing it.

`scripts/ui_acceptance.py` is a legacy opt-in UI check requiring pre-existing local fixtures and access-key mode. It is not part of the fresh-install procedure or CI. New onboarding changes should also be exercised with an isolated database in a browser, including failed setup, retry, refresh, and narrow layouts.
