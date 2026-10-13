"""Decision API contract from https://docs.litellm.ai/docs/decisions.

Synthetic probabilities exercise both paths; no live provider execution is claimed.
"""
import asyncio
import json
import subprocess
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from test_reviews import mock_github, payload, setup, webhook

from app import branch_conditions, fusion, review_sessions, runtime
from app.db import Job, ModelUsage, ReviewSession, Run, Session


def conditional_run(owner, monkeypatch, threshold=0.5, paths=None, model_gate=True):
    conn, _, agent, _, _ = setup(owner)
    agent['fusion'] = {'branches': [{'connection_id': conn['id'], 'model': 'specialist', 'paths': paths,
        'condition': {'connection_id': conn['id'], 'model': 'jev-test',
                      'instructions': 'Does this PR change LLM translations?', 'threshold': threshold} if model_gate else None}]}
    owner.put('/api/resources/agents/' + agent['id'], json=agent).raise_for_status()
    mock_github(monkeypatch)
    rid = webhook(owner, payload(body='@litereview')).json()['run_id']
    with Session.begin() as db:
        parent = next(j for j in db.scalars(select(Job).where(Job.run_id == rid)) if j.config.get('fusion'))
        child = fusion.branches(db, parent)[0]
        child.status = 'starting'
        return rid, parent.id, child.id


def decision_http(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(branch_conditions.httpx, 'AsyncClient',
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize('probability,expected', [(0.1, False), (0.5, True), (0.99, True)])
async def test_decisions_are_persisted_metered_and_false_never_starts_a_sandbox(owner, monkeypatch, probability, expected):
    rid, pid, jid = conditional_run(owner, monkeypatch)
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path.endswith('/decisions')
        body = json.loads(request.content)
        assert body['model'] == 'jev-test'
        assert json.loads(body['input']) == {'title': 'Test PR', 'body': ''}
        assert body['questions'][0]['type'] == 'predicate'
        assert 'max_tokens' not in body and 'max_output_tokens' not in body
        return httpx.Response(200, json={'answers': [{'type': 'predicate', 'name': 'run_branch', 'probability': probability}],
            'usage': {'input_tokens': 401, 'output_tokens': 0}}, headers={'x-litellm-response-cost': '0.000123'})

    decision_http(monkeypatch, respond)
    assert await branch_conditions.should_run(jid) is expected
    if expected:
        assert await branch_conditions.should_run(jid) is True  # Reuse the persisted decision after restart.
    else:
        def forbidden(*args):
            pytest.fail('A skipped branch must not create a sandbox')
        monkeypatch.setattr(runtime, 'create_sandbox', forbidden)
        await runtime.launch(jid, 'unused')
    assert len(requests) == 1
    with Session.begin() as db:
        job = db.get(Job, jid)
        assert job.config['branch_decision']['run'] is expected
        assert job.config['branch_decision']['probability'] == probability
        meter = db.scalar(select(ModelUsage).where(ModelUsage.job_id == jid))
        assert meter.model == 'jev-test' and meter.cost_usd == Decimal('0.000123') and meter.complete
        if not expected:
            assert job.result['skipped'] and not job.sandbox_id
            fusion.advance(db)
            parent = db.get(Job, pid)
            assert parent.status == 'completed' and parent.result['skipped']
            assert 'No code review was performed' in parent.result['summary']
            session = db.get(ReviewSession, job.config['session_id'])
            review_sessions.refresh(db, session)
            assert session.baseline_run_id is None
    if not expected:
        data = owner.get('/api/runs/' + rid).json()
        assert data['jobs'][0]['branches'][0]['result']['skipped']
        assert data['jobs'][0]['branches'][0]['config']['branch_decision']['run'] is False


@pytest.mark.parametrize('answer', [None, True, 'false', -0.1, 1.1, float('inf')])
async def test_invalid_decision_runs_the_branch_and_exposes_no_provider_details(owner, monkeypatch, answer):
    _, _, jid = conditional_run(owner, monkeypatch)
    def respond(request):
        if answer is None:
            return httpx.Response(503, text='private-provider-detail')
        return httpx.Response(200, content=json.dumps({'answers': [{'type': 'predicate', 'name': 'run_branch', 'probability': answer}]}))
    decision_http(monkeypatch, respond)
    assert await branch_conditions.should_run(jid)
    with Session() as db:
        job = db.get(Job, jid)
        assert job.status == 'starting'
        assert job.config['branch_decision']['error']
        assert 'private-provider-detail' not in str(job.config)


async def test_cancellation_wins_while_a_decision_is_in_flight(owner, monkeypatch):
    _, _, jid = conditional_run(owner, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    async def slow(*args):
        started.set()
        await release.wait()
        return {'run': True, 'probability': 0.9}
    monkeypatch.setattr(branch_conditions, 'decide', slow)
    task = asyncio.create_task(branch_conditions.should_run(jid))
    await started.wait()
    with Session.begin() as db:
        db.get(Job, jid).status = 'superseded'
    release.set()
    assert await task is False
    with Session() as db:
        assert 'branch_decision' not in db.get(Job, jid).config


def test_failed_and_skipped_branches_do_not_produce_a_successful_aggregate(owner, monkeypatch):
    _, pid, jid = conditional_run(owner, monkeypatch)
    with Session.begin() as db:
        child = db.get(Job, jid)
        child.status, child.result = 'completed', {'skipped': True}
        db.add(Job(id='failed-peer', run_id=child.run_id, status='failed', config={**child.config, 'fusion_index': 1}))
    with Session.begin() as db:
        fusion.advance(db)
        assert db.get(Job, pid).status == 'failed'


@pytest.mark.parametrize('path,pattern,expected', [
    ('litellm/llms/openai/chat/transformation.py', 'litellm/llms/**', True),
    ('litellm/llms/transformation.py', 'litellm/llms/**/transformation.py', True),
    ('litellm/llms/openai/chat/transformation.py', 'litellm/llms/*/transformation.py', False),
    ('litellm/llms/openai/transformation.py', 'litellm/llms/', True),
    ('litellm/llms_extra/transformation.py', 'litellm/llms/', False),
    ('litellm/litellm_core_utils/prompt_templates/factory.py', 'litellm/litellm_core_utils/prompt_templates/', True),
    ('src/app.py', 'src/app.py', True),
    ('src/app.py', 'src/app.?y', True),
    ('SRC/app.py', 'src/**', False),
])
def test_repository_relative_path_matching(path, pattern, expected):
    assert branch_conditions.path_matches(path, pattern) is expected


def test_paths_from_a_real_git_diff_include_renames_deletions_binary_spaces_and_unicode(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True)
    git('init', '-q')
    initial = {'old.py': b'renamed\n', 'deleted.py': b'deleted\n', 'binary.bin': b'\0before',
               'space name.py': b'before\n', 'caf\u00e9.py': b'before\n'}
    for name, data in initial.items():
        (tmp_path / name).write_bytes(data)
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'Before')
    (tmp_path / 'llms').mkdir()
    (tmp_path / 'old.py').rename(tmp_path / 'llms/transformation.py')
    (tmp_path / 'deleted.py').unlink()
    for name in ('binary.bin', 'space name.py', 'caf\u00e9.py'):
        (tmp_path / name).write_bytes(b'\0after' if name == 'binary.bin' else b'after\n')
    git('add', '.')
    diff = git('-c', 'core.quotePath=true', 'diff', '--cached', '-M')
    paths = branch_conditions.changed_paths(diff)
    assert set(paths) == {*initial, 'llms/transformation.py'}
    assert branch_conditions.evaluate_paths(['llms/'], diff)['matched'] == ['llms/transformation.py']
    assert branch_conditions.evaluate_paths(['old.py'], diff)['run']  # Also gate on the old name.


async def test_path_miss_skips_both_decision_call_and_sandbox(owner, monkeypatch):
    rid, pid, jid = conditional_run(owner, monkeypatch, paths=['litellm/llms/**'])
    async def forbidden_decision(*args):
        pytest.fail('Nonmatching paths must not spend decision-model credits')
    monkeypatch.setattr(branch_conditions, 'decide', forbidden_decision)
    monkeypatch.setattr(runtime, 'create_sandbox', lambda *args: pytest.fail('Must not allocate a sandbox'))
    await runtime.launch(jid, 'unused')
    with Session.begin() as db:
        job = db.get(Job, jid)
        assert job.result['skipped'] and job.status == 'completed'
        assert job.config['paths'] == ['litellm/llms/**']
        assert job.config['branch_decision']['paths']['matched'] == []
        assert 'no changed file' in job.result['summary']
        assert not db.scalar(select(ModelUsage).where(ModelUsage.job_id == jid))
        fusion.advance(db)
        assert db.get(Job, pid).result['skipped']
        session = db.get(ReviewSession, job.config['session_id'])
        review_sessions.refresh(db, session)
        assert session.baseline_run_id is None
    branch = owner.get('/api/runs/' + rid).json()['jobs'][0]['branches'][0]
    assert branch['config']['branch_decision']['paths']['patterns'] == ['litellm/llms/**']


async def test_path_only_uses_full_snapshot_not_incremental_diff_and_survives_restart(owner, monkeypatch):
    rid, _, jid = conditional_run(owner, monkeypatch, paths=['other.py', 'app.py'], model_gate=False)
    with Session.begin() as db:
        job = db.get(Job, jid)
        job.config = {**job.config, 'review_diff': 'diff --git a/docs.md b/docs.md\n+docs', 'review_scope': 'incremental'}
    assert await branch_conditions.should_run(jid)
    with Session.begin() as db:
        job = db.get(Job, jid)
        assert job.config['branch_decision']['paths']['matched'] == ['app.py']
        assert 'model' not in job.config['branch_decision']
        # The recorded gate is reused even if the source is unavailable on recovery.
        run = db.get(Run, rid)
        run.snapshot = {}
    assert await branch_conditions.should_run(jid)
    with Session() as db:
        assert db.get(Job, jid).config['branch_decision']['paths']['matched'] == ['app.py']


async def test_matching_paths_still_require_a_true_model_criterion(owner, monkeypatch):
    _, _, jid = conditional_run(owner, monkeypatch, paths=['app.py'])
    decision_http(monkeypatch, lambda request: httpx.Response(200, json={
        'answers': [{'type': 'predicate', 'name': 'run_branch', 'probability': 0.1}]}))
    assert not await branch_conditions.should_run(jid)
    with Session() as db:
        decision = db.get(Job, jid).config['branch_decision']
        assert decision['paths']['run'] and not decision['run']


@pytest.mark.parametrize('diff', [None, 'unavailable', 'diff --git a/ambiguous b/one b/two\n'])
async def test_unreadable_changed_paths_run_the_branch(owner, monkeypatch, diff):
    rid, _, jid = conditional_run(owner, monkeypatch, paths=['litellm/llms/**'])
    with Session.begin() as db:
        run = db.get(Run, rid)
        run.snapshot = {**run.snapshot, 'diff': diff}
    assert await branch_conditions.should_run(jid)
    with Session() as db:
        assert db.get(Job, jid).config['branch_decision']['paths']['error']


@pytest.mark.parametrize('paths', [['/absolute'], ['../escape'], ['!exclude/**'], ['src/**bad'], ['  '], ['src//app.py']])
def test_invalid_path_gate_is_rejected(owner, paths):
    conn, _, agent, _, _ = setup(owner)
    agent['fusion'] = {'branches': [{'connection_id': conn['id'], 'model': 'specialist', 'paths': paths}]}
    assert owner.put('/api/resources/agents/' + agent['id'], json=agent).status_code == 422
