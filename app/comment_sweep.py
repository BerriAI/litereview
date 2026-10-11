import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select

from . import github, reviews
from .db import Resource, Run, Session
from .settings import settings

logger = logging.getLogger(__name__)


async def _sweep_repository(repo, installation, since):
    started = 0
    try:
        for page in range(1, 11):
            response = await github.repo_request(
                repo,
                installation,
                "GET",
                "issues/comments",
                params={
                    "since": datetime.fromtimestamp(since, timezone.utc).isoformat(),
                    "sort": "created",
                    "direction": "desc",
                    "per_page": 100,
                    "page": page,
                },
            )
            comments = response.json()
            for comment in comments:
                created_at = datetime.fromisoformat(comment["created_at"].replace("Z", "+00:00"))
                if created_at.timestamp() < since:
                    return started
                updated_at = datetime.fromisoformat(comment["updated_at"].replace("Z", "+00:00"))
                if updated_at > created_at:
                    continue
                if comment.get("user", {}).get("type") == "Bot" or not reviews.is_command(
                    comment.get("body", "")
                ):
                    continue
                delivery = f"comment:{comment['id']}"
                with Session() as db:
                    if db.scalar(select(Run.id).where(Run.delivery == delivery)):
                        continue
                try:
                    number = int(comment["issue_url"].rstrip("/").rsplit("/", 1)[-1])
                    issue = (
                        await github.repo_request(repo, installation, "GET", f"issues/{number}")
                    ).json()
                    if "pull_request" not in issue:
                        continue
                    payload = {
                        "action": "created",
                        "issue": issue,
                        "comment": comment,
                        "repository": {"full_name": repo},
                        "installation": {"id": installation},
                    }
                    if await reviews.accept(payload, delivery):
                        started += 1
                except Exception:
                    logger.exception(
                        "Comment sweep failed for repository %s and comment %s",
                        repo,
                        comment["id"],
                    )
            if len(comments) < 100:
                break
    except Exception:
        logger.exception("Comment sweep failed for repository %s", repo)
    return started


async def sweep_once(since: float) -> int:
    if github.setup_state().get("disabled"):
        return 0
    with Session() as db:
        repositories = [
            (resource.data["name"], resource.data["installation_id"])
            for resource in db.scalars(select(Resource).where(Resource.kind == "repos"))
        ]
    started = 0
    for repo, installation in repositories:
        started += await _sweep_repository(repo, installation, since)
    return started


async def sweeper():
    started = time.time()
    while True:
        try:
            await asyncio.sleep(settings().comment_sweep_seconds)
            since = max(time.time() - settings().comment_sweep_lookback_seconds, started)
            await sweep_once(since)
        except Exception:
            logger.exception("Comment sweep iteration failed")
