import hashlib
import json
from datetime import datetime

import pytest

import schedule_bundle
import schedule_dispatch as dispatch
import schedule_handoff as handoff
import schedule_migration as migration
from schedules import KINDS, KST, cron_job, job_next_start


def test_signed_transfer_executes_one_slot_with_antigravity(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'target'
    key = 'trading-intraday-report'
    ident = f'{KINDS[1]}:{key}'
    cron = '30 11,13,14 * * 1-5'
    prompt = f'원본 프롬프트\n작업 위치 {handoff.REPO}\n런타임 {source}'
    job = {'id': ident, 'key': key, 'kind': KINDS[1], 'name': '장중 진행',
           'state': '등록됨', 'provider': 'Claude', 'model': 'claude-sonnet',
           'when': '평일 11:30 · 13:30 · 14:30',
           'schedule': {'cron': cron, 'timezone': 'Asia/Seoul', 'when': '평일 11:30 · 13:30 · 14:30'},
           'prompt': prompt, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(), 'dependency_hashes': {}}
    native = {'key': key, 'native': True, 'enabled': False, 'cron': cron, **cron_job(cron)}
    monkeypatch.setattr(schedule_bundle, 'inventory', lambda runtime: [job] if runtime == source else [])
    monkeypatch.setattr(handoff, 'claude_jobs', lambda: [native])
    monkeypatch.setattr(handoff, 'codex_jobs', lambda: [])
    monkeypatch.setattr(migration, 'claude_jobs', lambda: [])
    monkeypatch.setattr(migration, 'codex_jobs', lambda: [])
    monkeypatch.setattr(dispatch, 'claude_jobs', lambda: [])
    monkeypatch.setattr(dispatch, 'codex_jobs', lambda: [])
    monkeypatch.setattr(migration, 'profile_runner_ready', lambda _: True)
    target_id = handoff.device_id(target)
    source_key = handoff.device_info(source)['signing_fingerprint']
    old = {'task': key, 'provider': 'Claude', 'model': 'claude-sonnet', 'cron': cron,
           'saved': '2025-01-01T00:00:00+09:00'}
    with migration.closing(dispatch._open(source, write=True)) as db, db:
        db.execute('INSERT INTO routes VALUES (?, ?, ?, ?, ?)', tuple(old.values()))
    receipt = handoff.prepare(source, target_id, [ident])
    with pytest.raises(ValueError, match='지문'):
        handoff.validate_receipt(receipt, target_id, source_key='0' * 64)

    binary = tmp_path / 'agy'
    binary.write_text("""#!/usr/bin/env python3
import json, os, sys
assert os.environ.get('GEMINI_API_KEY') == 'unit-test-key'
model = sys.argv[sys.argv.index('--model') + 1]
prompt = json.loads(sys.stdin.readline())['message']['content']
if 'OMNI-CHECK' in prompt:
    answer = 'OMNI-CHECK:16'
elif 'Fixture:' in prompt:
    answer = '{"status":"normal"}'
else:
    assert '원본 프롬프트' in prompt
    assert '조회 자료' in prompt
    assert '/target' in prompt and '/source' not in prompt
    answer = '정상 장중 기록을 확인했습니다.'
print(json.dumps({'event':'init','init': {'model': model}}))
print(json.dumps({'event':'result','result': {'status':'SUCCESS','response': answer}}))
""")
    binary.chmod(0o700)
    credential = tmp_path / 'agy.key'
    credential.write_text('unit-test-key')
    credential.chmod(0o600)
    monkeypatch.setattr(dispatch, 'executable', lambda _: str(binary))
    profile = migration.register_profile(target, 'antigravity', 'test', 'gemini-test', credential_file=credential)
    assert migration.probe(target, profile['id'])['probe']['ok']
    plan = migration.create_plan(target, profile['id'], receipt=receipt, source_key=source_key)
    assert plan['rows'][0]['status'] == '대체 모델 검증 필요'
    fixture = tmp_path / 'fixture.json'
    fixture.write_text(json.dumps([{'input': {'event_count': n}, 'expected': {'status': 'unexpected'}} for n in (0, 1)]))
    assert migration.validate_model(target, plan['id'], ident, fixture)['rows'][0]['status'] == '대체 모델 검증 필요'
    with pytest.raises(ValueError, match='적용 가능한'):
        migration.apply(target, plan['id'])
    fixture.write_text(json.dumps([{'input': {'event_count': n}, 'expected': {'status': 'normal'}} for n in (0, 1)]))
    assert migration.validate_model(target, plan['id'], ident, fixture)['rows'][0]['validation']['passed'] == 2
    migration.apply(target, plan['id'])
    at = job_next_start({'always': False, 'state': '등록됨', **cron_job(cron)}, datetime.now(KST))
    assert not dispatch._claim(source, old, at)
    ready, = dispatch._due(target, at)
    route, slot = ready
    assert slot == at and route['cron'] == cron
    monkeypatch.setattr(dispatch, 'claude_jobs', lambda: [{'kind': KINDS[1], **native, 'enabled': True}])
    assert dispatch._due(target, at) == []
    dispatch._run(target, route, slot)
    assert dispatch.route_history(target, key) == []
    monkeypatch.setattr(dispatch, 'claude_jobs', lambda: [])
    import schedule_antigravity_report as report
    monkeypatch.setattr(report, 'facts', lambda *_: {'trading_day': True, 'events': {}})
    dispatch._run(target, route, slot)
    dispatch._run(target, route, slot)
    history = dispatch.route_history(target, key)
    assert len(history) == 1 and history[0]['model'] == 'gemini-test' and history[0]['status'] == '응답 기록 있음'
    assert migration.verify(target, plan['id'])['rows'][0]['first_run']['provider'] == 'Antigravity'
    assert migration.create_plan(target, profile['id'], receipt=receipt, source_key=source_key)['rows'][0]['status'] == '대기'
