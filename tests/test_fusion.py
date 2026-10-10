import time

import pytest
from sqlalchemy import select
from test_reviews import create, mock_github, payload, setup, webhook

from app import fusion, providers, reviews
from app.db import Job, Resource, Run, Session
from app.security import digest


def start_fusion(owner, monkeypatch):
    conn, _, agent, _, _ = setup(owner)
    second = create(owner, 'connections', name='Other', type='gateway', base_url='https://other.example/v1', api_key='second-secret')
    agent['fusion'] = {'branches': [
        {'connection_id': conn['id'], 'model': 'branch-a', 'reasoning_effort': 'high'},
        {'connection_id': second['id'], 'model': 'branch-b', 'focus': 'Trace real callers'},
    ], 'instructions': 'Preserve distinct verified bugs'}
    owner.put('/api/resources/agents/' + agent['id'], json=agent).raise_for_status()
    mock_github(monkeypatch)
    rid = webhook(owner, payload(body='@litereview')).json()['run_id']
    return rid, agent, second


def test_fusion_is_one_visible_review_with_immutable_independent_branches(owner, monkeypatch):
    rid, agent, second = start_fusion(owner, monkeypatch)
    owner.put('/api/resources/agents/' + agent['id'], json={**agent, 'instructions': 'Edited'}).raise_for_status()
    with Session() as db:
        jobs = list(db.scalars(select(Job).where(Job.run_id == rid)))
        roots = reviews.ordered_jobs(db.get(Run, rid), jobs)
        assert len(roots) == 1
        parent = roots[0]
        assert parent.status == 'waiting'
        children = fusion.branches(db, parent)
        assert [j.config['model'] for j in children] == ['branch-a', 'branch-b']
        assert all(j.status == 'queued' and 'Edited' not in j.config['instructions'] for j in children)
        assert children[1].config['connection']['base_url'] == 'https://other.example/v1'
        assert children[0].config['skills'][0]['instructions'] == 'Look for actionable issues.'
        assert 'Trace real callers' in children[1].config['instructions']
        assert 'fusion' not in children[0].config
        assert 'secret' not in str([j.config for j in jobs])
    data = owner.get('/api/runs/' + rid).json()
    assert len(data['jobs']) == 1
    assert len(data['jobs'][0]['branches']) == 2
    assert owner.delete('/api/resources/connections/' + second['id']).status_code == 409


@pytest.mark.parametrize('all_failed', [False, True])
def test_fusion_waits_for_every_branch_and_recovers_without_duplicate_aggregators(owner, monkeypatch, all_failed):
    rid, _, _ = start_fusion(owner, monkeypatch)
    with Session.begin() as db:
        parent = next(j for j in db.scalars(select(Job)) if j.status == 'waiting')
        children = fusion.branches(db, parent)
        children[0].status = 'failed' if all_failed else 'completed'
        children[0].result = {'findings': [], 'limitations': []}
        db.flush()
        fusion.advance(db)
        assert parent.status == 'waiting'
        children[1].status = 'timed_out'
        children[1].result = {'findings': [], 'limitations': ['Timed out']}
    with Session.begin() as db:
        fusion.advance(db)
    with Session.begin() as db:
        fusion.advance(db)
        jobs = list(db.scalars(select(Job).where(Job.run_id == rid)))
        assert len(jobs) == 3
        parent = next(j for j in jobs if j.config.get('fusion'))
        assert parent.status == ('failed' if all_failed else 'queued')
        if all_failed:
            assert 'No fusion branch' in parent.result['summary']
            assert len(parent.result['limitations']) == 3


def test_aggregator_bootstrap_and_finish_preserve_failed_branch_coverage(owner, monkeypatch):
    rid, _, _ = start_fusion(owner, monkeypatch)
    with Session.begin() as db:
        parent = next(j for j in db.scalars(select(Job)) if j.status == 'waiting')
        pid = parent.id
        for child in fusion.branches(db, parent):
            child.status = 'completed' if child.config['fusion_index'] == 0 else 'failed'
            child.result = {'summary': 'Independent report', 'findings': [], 'limitations': []}
        parent.status = 'starting'
        parent.token_hash = digest('scoped-token')
        parent.started_at = time.time()
    monkeypatch.setattr(providers, 'model_capacity', lambda _: {'context': 100000, 'output': 8192})
    auth = {'Authorization': 'Bearer scoped-token'}
    data = owner.get(f'/api/internal/jobs/{pid}/bootstrap', headers=auth).json()
    assert [r['status'] for r in data['branch_reports']] == ['completed', 'failed']
    response = owner.post(f'/api/internal/jobs/{pid}/finish', headers=auth, json={
        'status': 'completed', 'summary': 'Final report', 'findings': [], 'checks': [], 'limitations': [],
    })
    response.raise_for_status()
    with Session() as db:
        result = db.get(Job, pid).result
        assert any('Branch 2 (branch-b) did not complete' in x for x in result['limitations'])


def test_branch_connection_changes_invalidate_readiness(owner, monkeypatch):
    rid, _, second = start_fusion(owner, monkeypatch)
    with Session() as db:
        parent = next(j for j in db.scalars(select(Job).where(Job.run_id == rid)) if j.config.get('fusion'))
        resources = {r.id: r for r in db.scalars(select(Resource))}
        assert fusion.connections_current(parent.config, resources, time.time() + 1)
        resources[second['id']].updated_at = time.time() + 5
        assert not fusion.connections_current(parent.config, resources, time.time())
