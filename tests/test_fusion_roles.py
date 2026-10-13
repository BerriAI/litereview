"""Exercise saved role configuration through API acceptance and the real fusion queue."""

from unittest.mock import AsyncMock

from sqlalchemy import select
from test_reviews import create, mock_github, payload, setup, webhook

from app import agent_checks, fusion, providers, reviews
from app.db import Job, Run, Session
from app.schemas import AgentModel


def test_specialists_and_aggregator_have_independent_immutable_settings(owner, monkeypatch):
    conn, skill, agent, _, _ = setup(owner)
    specialist_skill = create(owner, 'skills', name='Reuse patterns', instructions='Find existing equivalents.')
    mcp = create(owner, 'connections', name='Search', type='mcp', base_url='https://tools.example.test/mcp')
    monkeypatch.setattr(providers, 'mcp_tools', AsyncMock(return_value=[{'name': 'search', 'inputSchema': {}}]))
    owner.post('/api/connections/' + mcp['id'] + '/test').raise_for_status()
    rule = {'id': 'reuse', 'name': 'Reuse', 'instructions': 'Check repository helpers.', 'required': True}
    agent.update(instructions='Verify all reports and write a concise conclusion.', reasoning_effort='high', fusion={'branches': [
        {'connection_id': conn['id'], 'model': 'correctness'},
        {'connection_id': conn['id'], 'model': 'drift', 'name': 'Drift and reuse',
         'instructions': 'Only find drift and reinvention.', 'skill_ids': [specialist_skill['id']],
         'rules': [rule], 'capabilities': ['browser'], 'mcps': [{'connection_id': mcp['id'], 'tools': ['search']}]},
        {'connection_id': conn['id'], 'model': 'bare', 'instructions': 'Only review interfaces.',
         'skill_ids': [], 'mcps': [], 'rules': [], 'capabilities': []},
    ]})
    saved = owner.put('/api/resources/agents/' + agent['id'], json=agent)
    saved.raise_for_status()
    mock_github(monkeypatch)
    rid = webhook(owner, payload(body='@litereview')).json()['run_id']
    owner.put('/api/resources/skills/' + specialist_skill['id'], json={**specialist_skill, 'instructions': 'Edited'}).raise_for_status()
    with Session() as db:
        parent = reviews.ordered_jobs(db.get(Run, rid), list(db.scalars(select(Job).where(Job.run_id == rid))))[0]
        a, b, c = fusion.branches(db, parent)
        assert 'Verify all reports' in parent.config['instructions']
        assert parent.config['skills'][0]['id'] == skill['id']
        assert a.config['skills'] == parent.config['skills']
        assert parent.config['reasoning_effort'] == 'high' and a.config['reasoning_effort'] is None
        assert 'Verify all reports' in a.config['instructions']
        assert 'Verify all reports' not in b.config['instructions']
        assert b.config['skills'][0]['instructions'] == 'Find existing equivalents.'
        assert b.config['rules'][0]['id'] == 'reuse'
        assert b.config['capabilities'] == ['terminal', 'browser']
        assert b.config['mcp_definitions'][0]['tools'][0]['name'] == 'search'
        assert b.config['fusion_role'] == 'Drift and reuse'
        assert c.config['skills'] == c.config['rules'] == c.config['mcp_definitions'] == []
        assert c.config['capabilities'] == ['terminal']
    for kind, resource in [('skills', specialist_skill), ('connections', mcp)]:
        assert owner.delete('/api/resources/' + kind + '/' + resource['id']).status_code == 409
    assert owner.get('/api/runs/' + rid).json()['jobs'][0]['branches'][1]['config']['fusion_role'] == 'Drift and reuse'
    invalid = {**agent, 'fusion': {'branches': [{'connection_id': conn['id'], 'model': 'drift', 'skill_ids': ['missing']}]}}
    assert owner.put('/api/resources/agents/' + agent['id'], json=invalid).status_code == 400


def test_private_checks_never_execute_role_instructions_skills_mcps_or_conditions(owner):
    conn, skill, _, _, _ = setup(owner)
    data = AgentModel(connection_id=conn['id'], model='final', fusion={'branches': [{
        'connection_id': conn['id'], 'model': 'branch', 'instructions': 'DO NOT RUN THIS',
        'skill_ids': [skill['id']], 'rules': [{'name': 'Production', 'instructions': 'DO NOT RUN'}],
        'mcps': [{'connection_id': 'not-a-real-server', 'tools': ['side_effect']}], 'capabilities': ['browser'],
        'paths': ['src/**'], 'condition': {'connection_id': conn['id'], 'model': 'decision', 'instructions': 'DO NOT ASK THIS'},
    }]})
    check = agent_checks.enqueue(data, {'id': conn['id'], 'type': 'gateway', 'base_url': 'https://model.example'})
    with Session() as db:
        parent = next(j for j in db.scalars(select(Job).where(Job.run_id == check['id'])) if j.config.get('fusion'))
        branch = fusion.branches(db, parent)[0]
        assert 'DO NOT' not in str(branch.config)
        assert branch.config['skill_ids'] == ['check-skill']
        assert branch.config['mcps'] == branch.config['rules'] == []
        assert branch.config['capabilities'] == ['terminal', 'browser']
        assert 'Use the browser tool' in branch.config['instructions']
        assert fusion.selection(parent.config) == fusion.selection(data.model_dump())
        assert 'condition' not in branch.config and 'paths' not in branch.config
