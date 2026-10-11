# Contributing to LiteReview

Start with the [local development guide](docs/DEVELOPMENT.md). Use Python 3.12, Node 22, and uv 0.10.9. Core automated tests use disposable fixtures and do not require Modal, GitHub, or model credentials.

## Propose a change

Open an issue for a substantial behavior or architecture change. For a small fix, open a pull request describing the problem, resulting behavior, and checks you ran. Include screenshots for visible UI changes and a regression test for meaningful behavior changes. Follow the existing patterns in [the UI guide](docs/UI.md); keep setup instructions aligned with the product.

Run from the repository root:

```sh
uv sync --frozen --python 3.12
npm ci --prefix frontend
uv run ruff check app runner tests
uv run pytest -q
npm run build --prefix frontend
```

CI also runs the tests against PostgreSQL and builds the Docker image. The frontend build packages license notices for production dependencies. Update `uv.lock` or `frontend/package-lock.json` alongside dependency changes, inspect the dependency's license, and keep [third-party notices](THIRD_PARTY_NOTICES.md) accurate. New dependencies without license text stop the frontend build.

Live checks are opt-in: they execute code and consume Modal/model credits. Use a disposable deployment, accounts and repositories you control, and test-only credentials. [Validation](docs/VALIDATION.md) distinguishes local tests from live integration checks. Never commit `.env`, keys, database files, private source snapshots, or test evidence.

## License and provenance

By submitting a contribution, you agree that your original contribution is provided under this repository's MIT license. Submit only work you have the right to contribute. Identify third-party code and assets, preserve their licenses and attribution, and do not copy material from private repositories without permission. Product and provider trademarks retain their owners' rights.

For vulnerabilities, use the private contact in [SECURITY.md](SECURITY.md).
