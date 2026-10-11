import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from . import github, reviews
from .db import CommentSweepCursor, Resource, Session
from .settings import settings

logger = logging.getLogger(__name__)


async def _sweep_repository(repo, installation, since):
    started = 0
    complete = True
    try:
        page = 1
        while True:
            # Bound outbound polling requests so one unavailable repository
            # cannot stall recovery for every other repository.
            async with asyncio.timeout(30):
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
                    return started, complete
                updated_at = datetime.fromisoformat(comment["updated_at"].replace("Z", "+00:00"))
                if updated_at > created_at:
                    continue
                if comment.get("user", {}).get("type") == "Bot" or not reviews.is_command(
                    comment.get("body", "")
                ):
                    continue
                delivery = f"comment:{comment['id']}"
                with Session() as db:
                    if reviews.comment_run(db, repo, comment["id"]):
                        continue
                try:
                    async with asyncio.timeout(60):
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
                    complete = False
                    logger.exception(
                        "Comment sweep failed for repository %s and comment %s",
                        repo,
                        comment["id"],
                    )
            if len(comments) < 100:
                break
            page += 1
    except Exception:
        complete = False
        logger.exception("Comment sweep failed for repository %s", repo)
    return started, complete


def _checkpoint(key, initial):
    with Session() as db:
        saved = db.get(CommentSweepCursor, key)
        if not saved:
            saved = CommentSweepCursor(key=key, scanned_through=initial)
            db.add(saved)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                saved = db.get(CommentSweepCursor, key)
        return saved.scanned_through


async def sweep_once(since: float | None = None) -> int:
    if github.setup_state().get("disabled"):
        return 0
    with Session() as db:
        repositories = [
            (resource.id, resource.data["name"], resource.data["installation_id"])
            for resource in db.scalars(select(Resource).where(Resource.kind == "repos"))
        ]
    started = 0
    for repo_id, repo, installation in repositories:
        scanned_at = time.time()
        key = f"{repo_id}:{installation}"
        lookback = scanned_at - settings().comment_sweep_lookback_seconds
        # Save the initial boundary before network I/O, including when the very
        # first request fails. Downtime must not age a missed comment out.
        boundary = since if since is not None else min(_checkpoint(key, lookback), lookback)
        count, complete = await _sweep_repository(repo, installation, boundary)
        started += count
        if complete and since is None:
            with Session.begin() as db:
                db.execute(update(CommentSweepCursor).where(
                    CommentSweepCursor.key == key,
                    CommentSweepCursor.scanned_through < scanned_at,
                ).values(scanned_through=scanned_at))
    return started


async def sweeper():
    while True:
        try:
            await sweep_once()
        except Exception:
            logger.exception("Comment sweep iteration failed")
        await asyncio.sleep(settings().comment_sweep_seconds)
