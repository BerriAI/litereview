# Develop locally

This opens the UI and API without cloud credentials. A real review still requires the external services described below. Use the commands in a macOS or Linux shell.

Install [Python 3.12](https://www.python.org/downloads/), [Node 22](https://nodejs.org/), and [uv 0.10.9](https://docs.astral.sh/uv/getting-started/installation/). Clone the repository and install dependencies:

```sh
git clone https://github.com/BerriAI/litereview.git
cd litereview
uv sync --frozen --python 3.12
npm ci --prefix frontend
npm run build --prefix frontend
```

In a new checkout, create a local configuration with independent random keys. This command refuses to overwrite an existing `.env`:

```sh
uv run python - <<'PY'
from pathlib import Path
import secrets

text = Path('.env.example').read_text()
for name in ('SECRET_KEY', 'ADMIN_TOKEN'):
    text = text.replace(f'{name}=\n', f'{name}={secrets.token_urlsafe(32)}\n')
with Path('.env').open('x') as output:
    output.write(text)
Path('.env').chmod(0o600)
PY
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open `http://localhost:8000` and sign in with the generated `ADMIN_TOKEN` from `.env` in **Workspace access key**. In another terminal, `curl --fail http://localhost:8000/health` should return `{"status":"ok"}`. This starts the workspace; live reviews still need the external accounts listed in the [deployment guide](DEPLOYMENT.md#prepare-your-accounts).

## Use Docker Compose or a live review

SQLite is supported for local development. Use Postgres in deployment. After creating `.env`, `docker compose up --build` runs both services locally. Use one API worker and one service instance in v1.

Modal must reach the API over HTTPS. For local live testing, expose port 8000 through a temporary HTTPS tunnel and set `PUBLIC_URL` to its address before starting the app. All configuration, results, and screenshots require sign-in; job relay endpoints require expiring per-job tokens. `PUBLIC_URL` must also match the origin you use in your browser.

For UI iteration, set `PUBLIC_URL=http://localhost:5173` in `.env`, restart the API, and run the UI in another terminal:

```sh
npm run dev --prefix frontend
```

Open `http://localhost:5173`. Vite proxies API calls to port 8000. Use this origin for local UI-only work.

## Run checks

```sh
uv run ruff check app runner tests
uv run pytest -q
npm run build --prefix frontend
```

`uv run python -m scripts.modal_smoke` exercises browser and desktop tools in a real Modal sandbox and consumes Modal credits. Live review tests additionally consume model credits.

For the full LiteReview app check, run `uv run python -m scripts.modal_app_smoke --repo YOUR-OWNER/YOUR-LITEREVIEW-FORK --pr YOUR-PR-NUMBER` against a PR in your LiteReview fork. Replace both placeholders with a repository and trusted PR you control. This downloads its pinned source, verifies every extracted file, installs dependencies, builds and starts the application, exercises browser/desktop tools and internet access, and asks the configured gateway model to read a screenshot. It requires authenticated `gh` and local Modal/gateway credentials; evidence stays in ignored `.test-artifacts/` files. It executes the selected PR's code and consumes Modal/model credits.

## Stop and resume

Use **Ctrl+C** to stop the source server. For Compose, run `docker compose down` from the repository root; this retains the database volume. Keep `.env` and its `SECRET_KEY` to reuse saved connections. Do not use `down --volumes` unless you intend to delete the local database.
