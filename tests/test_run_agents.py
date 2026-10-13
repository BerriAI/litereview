from app.db import Job, Run, Session


def test_run_agent_names_and_filter_deduplicate_instances_and_preserve_history(owner):
    with Session.begin() as db:
        for rid in ('one', 'two', 'check', 'shadow-only'):
            db.add(Run(id=rid, delivery=rid, repo='example/repo', pr=1, actor='alice',
                       head='a', base='b', snapshot={'harness_check': rid == 'check'}))
        for jid, rid, agent, extras in [
            ('a1', 'one', 'a', {}), ('a2', 'one', 'a', {}), ('b1', 'one', 'b', {}),
            ('a3', 'two', 'a', {}), ('check-a', 'check', 'a', {}),
            ('shadow', 'shadow-only', 'a', {'comparison_parent_id': 'not-primary'}),
            ('branch', 'one', 'a', {'fusion_parent_id': 'a1'}),
        ]:
            db.add(Job(id=jid, run_id=rid, config={'agent_id': agent, 'name': 'saved-' + agent, **extras}))
    response = owner.get('/api/runs', params={'agent_id': 'a', 'limit': 1}).json()
    assert response['total'] == 2 and response['has_more']
    next_page = owner.get('/api/runs', params={'agent_id': 'a', 'offset': 1, 'limit': 1}).json()
    assert next_page['total'] == 2 and not next_page['has_more']
    rows = {r['id']: r for r in response['items'] + next_page['items']}
    assert rows['one']['agents'] == [{'id': 'a', 'name': 'saved-a'}, {'id': 'b', 'name': 'saved-b'}]
    assert rows['two']['agents'] == [{'id': 'a', 'name': 'saved-a'}]
    assert owner.get('/api/runs', params={'agent_id': 'b', 'limit': 30}).json()['total'] == 1
    assert owner.get('/api/runs', params={'agent_id': 'missing', 'limit': 30}).json()['items'] == []
    assert owner.get('/api/runs', params={'agent_id': 'a', 'query': 'unrelated', 'limit': 30}).json()['total'] == 0
