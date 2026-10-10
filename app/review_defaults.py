"""Resolve the workspace's default agent without changing named commands."""

from sqlalchemy import select

from .db import Resource

RESOURCE_ID = "default-reviewer"


def selected(resources):
    saved = resources.get(RESOURCE_ID)
    if saved:
        agent = resources.get(saved.data.get("agent_id"))
        return agent if agent and agent.kind == "agents" else None
    # Existing workspaces with one reviewer need no extra setup decision.
    agents = [r for r in resources.values() if r.kind == "agents"]
    return agents[0] if len(agents) == 1 else None


def agent_id(db):
    agent = selected({r.id: r for r in db.scalars(select(Resource))})
    return agent.id if agent else None


def preserve_on_create(db, new_id):
    saved = db.get(Resource, RESOURCE_ID, with_for_update=True)
    agents = db.scalars(select(Resource).where(Resource.kind == "agents")).all()
    # Preserve a legacy sole reviewer's implicit default when adding a second.
    if not agents or (not saved and len(agents) == 1):
        chosen = agents[0].id if agents else new_id
        if not saved:
            saved = Resource(id=RESOURCE_ID, kind="system", data={})
            db.add(saved)
        saved.data = {"agent_id": chosen}
