import hashlib
import json

import pytest

import schedule_bundle
import schedule_dispatch as dispatch
import schedule_migration as migration


def test_plan_model_validation_apply_and_rollback(tmp_path, monkeypatch):
    key = 'intraday-audit-hourly'
    ident = f'{migration.KINDS[1]}:{key}'
    prompt = tmp_path / 'SKILL.md'
    prompt.write_text('Compare audit outcomes.', encoding='utf-8')
    source = {'id': ident, 'key': key, 'kind': migration.KINDS[1], 'name': '감사', 'when': '평일 09:20',
              'schedule': {'cron': '20 9 * * 1-5'}, 'prompt_sha256': hashlib.sha256(prompt.read_bytes()).hexdigest(),
              'state': '등록됨', 'provider': 'Claude', 'model': 'sonnet'}
    native = {'key': key, 'enabled': True, 'native': True, 'times': [(9, 20)], 'cron': '20 9 * * 1-5'}
    monkeypatch.setattr(schedule_bundle, 'inventory', lambda _: [source])
    monkeypatch.setattr(migration, 'claude_jobs', lambda: [native])
    monkeypatch.setattr(migration, 'codex_jobs', lambda: [])
    monkeypatch.setattr(migration, '_account', lambda _: 'account-fingerprint')
    monkeypatch.setattr(migration, 'profile_runner_ready', lambda _: True)
    monkeypatch.setattr(dispatch, '_skill', lambda _: prompt)
    migration.register_profile(tmp_path, 'codex', 'work', 'target')
    monkeypatch.setattr(migration, '_test_model', lambda *_: ('OMNI-CHECK:16', 'target'))
    assert migration.probe(tmp_path, 'codex:work')['probe']['ok']
    plan = migration.create_plan(tmp_path, 'codex:work')
    assert plan['rows'][0]['status'] == '대체 모델 검증 필요'
    fixture = tmp_path / 'fixture.json'
    fixture.write_text(json.dumps([{'input': {'n': n}, 'expected': {'ok': True}} for n in (0, 1)]))
    monkeypatch.setattr(migration, '_test_model', lambda *_: ('{"ok":true}', 'target'))
    assert migration.validate_model(tmp_path, plan['id'], ident, fixture)['rows'][0]['status'] == '이전 가능'
    with pytest.raises(ValueError, match='일시 정지'):
        migration.apply(tmp_path, plan['id'])
    native['enabled'] = False
    result = migration.apply(tmp_path, plan['id'])
    assert result['state'] == '적용됨' and result['rows'][0]['first_run'] is None
    with migration.closing(migration._db(tmp_path)) as db:
        route = dict(db.execute('SELECT * FROM routes').fetchone())
    assert migration.bound_profile(tmp_path, route)['id'] == 'codex:work'
    prompt.write_text('Changed instructions.', encoding='utf-8')
    with pytest.raises(ValueError, match='변경'):
        migration.bound_profile(tmp_path, route)
    assert migration.rollback(tmp_path, plan['id'])['state'] == '되돌림'
    with migration.closing(migration._db(tmp_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM routes').fetchone()[0] == 0
