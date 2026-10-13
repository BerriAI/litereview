import json
import time
from contextlib import asynccontextmanager

import anyio
import httpx
import jwt
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .db import Resource, Session
from .security import decrypt
from .settings import settings

API = "https://api.github.com"


class GitHubError(HTTPException):
    def __init__(self, response, method, path):
        self.upstream_status = response.status_code
        try:
            self.errors = response.json().get("errors", [])
        except (ValueError, AttributeError):
            self.errors = []
        super().__init__(
            502,
            f"GitHub returned {response.status_code} for {method} {path}. Check the App installation and repository permissions.",
        )


def setup_state():
    with Session() as db:
        saved = db.get(Resource, "github")
        return saved.data if saved else {}


def require_enabled():
    if setup_state().get("disabled"):
        raise HTTPException(409, "GitHub setup was reset. Connect an App or undo the reset in Repositories.")


def config():
    with Session() as db:
        saved = db.get(Resource, "github")
        if saved and saved.data.get("disabled"):
            return {}
        data = json.loads(decrypt(saved.secret)) if saved and saved.secret else {}
        if saved and saved.data.get("overrides_environment"):
            return data
    s = settings()
    for field in ("app_id", "private_key", "webhook_secret", "client_id", "client_secret"):
        value = getattr(s, "github_" + field)
        if value:
            data[field] = value.replace("\\n", "\n") if field == "private_key" else value
    return data


def app_jwt():
    c = config()
    if not c.get("app_id") or not c.get("private_key"):
        raise HTTPException(503, "Create or connect a GitHub App in Repositories.")
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
    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.request(method, API + path, headers=headers, **kwargs)
    if not response.is_success:
        raise GitHubError(response, method, path)
    return response


async def token(installation):
    require_enabled()
    if settings().github_token and not installation:
        return settings().github_token
    response = await request("POST", f"/app/installations/{installation}/access_tokens")
    return response.json()["token"]


async def repo_request(repo, installation, method, suffix, **kwargs):
    return await request(method, f"/repos/{repo}/{suffix}", await token(installation), **kwargs)


async def resolve_review_thread(repo, installation, number, comment_id, *, expected_head, resolved=True):
    owner, name = repo.split("/", 1)
    auth, cursor = await token(installation), None
    while True:
        response = (await request("POST", "/graphql", auth, json={
            "query": """query($owner:String!, $name:String!, $number:Int!, $cursor:String) {
              repository(owner:$owner,name:$name) { pullRequest(number:$number) {
                reviewThreads(first:100,after:$cursor) {
                  nodes { id isResolved viewerCanResolve viewerCanUnresolve
                    comments(first:1) { nodes { fullDatabaseId } } }
                  pageInfo { hasNextPage endCursor }
                }
              } }
            }""",
            "variables": {"owner": owner, "name": name, "number": number, "cursor": cursor},
        })).json()
        if response.get("errors"):
            raise HTTPException(502, "GitHub could not read the finding thread. Check Pull requests write permission.")
        threads = response["data"]["repository"]["pullRequest"]["reviewThreads"]
        for thread in threads["nodes"]:
            if any(str(c["fullDatabaseId"]) == str(comment_id) for c in thread["comments"]["nodes"]):
                if thread["isResolved"] != resolved:
                    capability = "viewerCanResolve" if resolved else "viewerCanUnresolve"
                    action = "resolve" if resolved else "reopen"
                    if thread.get(capability) is False:
                        raise HTTPException(403, f"GitHub does not allow this App to {action} this thread. A repository maintainer must do it manually.")
                    current = (await repo_request(repo, installation, "GET", f"pulls/{number}")).json()
                    if current["head"]["sha"] != expected_head or current.get("state", "open") != "open":
                        raise HTTPException(409, "The PR changed before the thread could be updated.")
                    mutation = "resolveReviewThread" if resolved else "unresolveReviewThread"
                    result = (await request("POST", "/graphql", auth, json={
                        "query": f"mutation($id:ID!){{ {mutation}(input:{{threadId:$id}}) {{ thread {{ isResolved }} }} }}",
                        "variables": {"id": thread["id"]},
                    })).json()
                    if result.get("errors"):
                        errors = result["errors"]
                        detail = "; ".join(e.get("message", "Unknown GraphQL error") for e in errors)
                        denied = any(e.get("type") == "FORBIDDEN" for e in errors)
                        raise HTTPException(403 if denied else 502, f"GitHub could not {action} the finding thread: {detail}")
                    payload = (result.get("data") or {}).get(mutation) or {}
                    if (payload.get("thread") or {}).get("isResolved") is not resolved:
                        raise HTTPException(502, f"GitHub did not confirm that the finding thread was {'resolved' if resolved else 'reopened'}.")
                return
        if not threads["pageInfo"]["hasNextPage"]:
            raise HTTPException(502, "The original finding thread is no longer available.")
        cursor = threads["pageInfo"]["endCursor"]


async def permission(repo, installation, user, expected_id=None):
    response = await repo_request(repo, installation, "GET", f"collaborators/{user}/permission")
    data = response.json()
    if expected_id and str(data.get("user", {}).get("id", "")) != str(expected_id):
        return "none"
    return data.get("permission", "none")


@asynccontextmanager
async def archive(repo, installation, sha):
    # Authenticate only to GitHub. Follow the redirect without the installation header.
    auth = await token(installation)
    client = httpx.AsyncClient(timeout=None)
    stream = None
    try:
        r = await client.get(f"{API}/repos/{repo}/tarball/{sha}", headers={"Authorization": f"Bearer {auth}"})
        if r.status_code not in (301, 302, 307):
            raise HTTPException(502, "GitHub did not provide a repository archive.")
        url = r.headers["location"]
        from urllib.parse import urlparse

        if urlparse(url).scheme != "https" or urlparse(url).hostname != "codeload.github.com":
            raise HTTPException(502, "Unexpected GitHub archive host.")
        stream = await client.send(client.build_request("GET", url), stream=True)
        if not stream.is_success:
            raise HTTPException(502, f"GitHub repository download failed (HTTP {stream.status_code}).")
        yield stream
    except httpx.HTTPError:
        raise HTTPException(502, "GitHub repository download was interrupted. Retry the review.") from None
    finally:
        # A disconnected sandbox must also release the upstream connection.
        with anyio.CancelScope(shield=True):
            if stream is not None:
                await stream.aclose()
            await client.aclose()


class ArchiveResponse(StreamingResponse):
    def __init__(self, repo, installation, sha):
        super().__init__(iter(()), media_type="application/gzip")
        self.archive = archive(repo, installation, sha)

    async def __call__(self, scope, receive, send):
        # Open upstream before sending headers so download failures retain their detail.
        async with self.archive as stream:
            self.body_iterator = stream.aiter_bytes(chunk_size=64 * 1024)
            await super().__call__(scope, receive, send)


async def installations():
    rows, page = [], 1
    while True:
        batch = (await request("GET", f"/app/installations?per_page=100&page={page}")).json()
        rows.extend(batch)
        if len(batch) < 100:
            return rows
        page += 1


async def installed_repositories(installs=None):
    require_enabled()
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
        "public": app.get("public"),
        "review_events_enabled": "pull_request" in app.get("events", []),
        "reply_events_enabled": "pull_request_review_comment" in app.get("events", []),
        "repositories": sorted(repos, key=lambda r: r["name"].lower()),
        "installations": [
            {"account": i["account"]["login"], "suspended": bool(i.get("suspended_at")),
             "installation_id": i["id"], "manage_url": i.get("html_url", "")} for i in installs
        ],
    }
