import json
import time

import httpx
import jwt
from fastapi import HTTPException

from .db import Resource, Session
from .security import decrypt
from .settings import settings

API = "https://api.github.com"


def config():
    with Session() as db:
        saved = db.get(Resource, "github")
        data = json.loads(decrypt(saved.secret)) if saved and saved.secret else {}
    s = settings()
    for field in ("app_id", "private_key", "webhook_secret", "client_id", "client_secret"):
        value = getattr(s, "github_" + field)
        if value:
            data[field] = value.replace("\\n", "\n") if field == "private_key" else value
    return data


def app_jwt():
    c = config()
    if not c.get("app_id") or not c.get("private_key"):
        raise HTTPException(503, "Create or connect a GitHub App in Connections → GitHub.")
    return jwt.encode(
        {"iat": int(time.time()) - 60, "exp": int(time.time()) + 540, "iss": c["app_id"]},
        c["private_key"],
        algorithm="RS256",
    )


async def request(method, path, token=None, **kwargs):
    headers = {
        "Authorization": f"Bearer {token or app_jwt()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    headers.update(kwargs.pop("headers", {}))
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.request(method, API + path, headers=headers, **kwargs)
    if not response.is_success:
        raise HTTPException(
            502,
            f"GitHub returned {response.status_code} for {method} {path}. Check the App installation and repository permissions.",
        )
    return response


async def token(installation):
    if settings().github_token and not installation:
        return settings().github_token
    response = await request("POST", f"/app/installations/{installation}/access_tokens")
    return response.json()["token"]


async def repo_request(repo, installation, method, suffix, **kwargs):
    return await request(method, f"/repos/{repo}/{suffix}", await token(installation), **kwargs)


async def permission(repo, installation, user):
    response = await repo_request(repo, installation, "GET", f"collaborators/{user}/permission")
    return response.json().get("permission", "none")


async def archive(repo, installation, sha):
    # Authenticate only to GitHub. Follow the redirect without the installation header.
    auth = await token(installation)
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.get(f"{API}/repos/{repo}/tarball/{sha}", headers={"Authorization": f"Bearer {auth}"})
        if r.status_code not in (301, 302, 307):
            raise HTTPException(502, "GitHub did not provide a repository archive.")
        url = r.headers["location"]
        from urllib.parse import urlparse

        if urlparse(url).hostname != "codeload.github.com":
            raise HTTPException(502, "Unexpected GitHub archive host.")
        chunks, total = [], 0
        async with client.stream("GET", url) as stream:
            stream.raise_for_status()
            async for chunk in stream.aiter_bytes():
                total += len(chunk)
                if total > 50_000_000:
                    raise HTTPException(413, "Repository archive exceeds the v1 limit of 50 MB.")
                chunks.append(chunk)
        return b"".join(chunks)


async def installations():
    rows, page = [], 1
    while True:
        batch = (await request("GET", f"/app/installations?per_page=100&page={page}")).json()
        rows.extend(batch)
        if len(batch) < 100:
            return rows
        page += 1


async def installed_repositories(installs=None):
    if settings().github_token and not config().get("app_id"):
        r = await request("GET", "/user/repos?per_page=100&sort=updated", settings().github_token)
        return [{"name": x["full_name"], "installation_id": 0} for x in r.json()]
    installs = await installations() if installs is None else installs
    repos = []
    for install in installs:
        if install.get("suspended_at"):
            continue
        auth = await token(install["id"])
        page = 1
        while True:
            rows = (
                await request("GET", f"/installation/repositories?per_page=100&page={page}", auth)
            ).json()["repositories"]
            repos.extend({"name": r["full_name"], "installation_id": install["id"]} for r in rows)
            if len(rows) < 100:
                break
            page += 1
    return repos


async def connection_status():
    """Verify the App separately from its installation and repository access."""
    conf = config()
    if not (conf.get("app_id") and conf.get("private_key")):
        return {"state": "unconfigured", "repositories": [], "installations": []}
    app = (await request("GET", "/app")).json()
    installs = await installations()
    repos = await installed_repositories(installs)
    return {
        "state": "available" if repos else "needs_installation",
        "app_name": app["name"],
        "app_url": app["html_url"],
        "owner": app.get("owner", {}).get("login", ""),
        "owner_type": app.get("owner", {}).get("type", ""),
        "repositories": sorted(repos, key=lambda r: r["name"].lower()),
        "installations": [
            {"account": i["account"]["login"], "suspended": bool(i.get("suspended_at"))} for i in installs
        ],
    }
