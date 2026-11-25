import json
from contextlib import closing
from datetime import datetime, timedelta

import pytest

import schedule_dispatch as D
from schedules import KINDS, KST, WEEKDAYS


TASK = 'intraday-audit-hourly'


@pytest.fixture
def schedule(tmp_path, monkeypatch):
    job = {'kind': KINDS[1], 'key': TASK, 'native': True, 'enabled': True, 'cron': '20 9-15 * * 1-5',
           'days': WEEKDAYS, 'times': [(9, 20)], 'always': False, 'monthday': None, 'date': None, 'state': '등록됨'}
    peers = []
    monkeypatch.setattr(D, 'claude_jobs', lambda: [job])
    monkeypatch.setattr(D, 'codex_jobs', lambda: peers)
    monkeypatch.setattr(D, 'executable', lambda _: '/model')
    monkeypatch.setattr(D, 'runner_ready', lambda: True)
    return job, peers, tmp_path


def test_switch_preserves_schedule_and_requires_native_pause(schedule):
    job, peers, runtime = schedule
    D.set_route(runtime, TASK, 'Codex', 'gpt-6.2-sol')
    routes, recent = D.route_rows(runtime)
    route = routes[TASK]
    assert route['cron'] == job['cron'] and not route['ready'] and not recent
    assert job['enabled'] is True
    start = datetime(2030, 4, 1, 9, 20, 13, tzinfo=KST)
    assert D._due(runtime, start) == []
    job['enabled'] = False
    peers.append({'key': 'automation', 'state': '등록됨'})
    assert D._due(runtime, start) == []
    peers[0]['state'] = '일시 정지'
    assert D._due(runtime, start)[0][1] == start.replace(second=0)
    assert D._due(runtime, start + timedelta(minutes=1)) == []
    assert D.route_rows(runtime)[0][TASK]['ready'] is True
    job['cron'] = '21 9-15 * * 1-5'
    assert D._due(runtime, start) == []
    D.set_route(runtime, TASK, '', '')
    assert D.route_rows(runtime)[0] == {}


@pytest.mark.parametrize('provider', ('Claude', 'Codex'))
def test_dispatch_uses_selected_cli_model_and_records_one_result(schedule, monkeypatch, provider):
    job, _, runtime = schedule
    job['enabled'] = False
    chosen = D.OPTIONS[provider][0]
    prompt = runtime / 'SKILL.md'
    prompt.write_text('VERIFICATION_TOKEN', encoding='utf-8')
    monkeypatch.setattr(D, '_skill', lambda key: prompt)
    monkeypatch.setattr(D, 'REPO', runtime)
    model = 'simulated-model-used'
    output = (json.dumps({'type': 'result', 'is_error': False, 'result': 'ok', 'modelUsage': {model: {}}})
              if provider == 'Claude' else '\n'.join(map(json.dumps, [
                  {'type': 'thread.started', 'model': model},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'ok'}},
                  {'type': 'turn.completed'},
              ])))
    binary = runtime / provider
    binary.write_text(f"""#!/usr/bin/env python3
import os
import sys
assert {chosen!r} in sys.argv
assert 'VERIFY_SECRET' not in os.environ
assert 'VERIFICATION_TOKEN' in sys.stdin.read()
print({output!r})
""")
    binary.chmod(0o700)
    monkeypatch.setenv('VERIFY_SECRET', 'should-never-be-inherited')
    monkeypatch.setattr(D, 'executable', lambda _: str(binary))
    D.set_route(runtime, TASK, provider, chosen)
    route = D.route_rows(runtime)[0][TASK]
    slot = datetime(2030, 4, 1, 9, 20, tzinfo=KST)
    D._run(runtime, route, slot)
    D._run(runtime, route, slot)
    history = D.route_history(runtime, TASK)
    assert len(history) == 1
    assert (history[0]['provider'], history[0]['model'], history[0]['status'], history[0]['result']) == (
        provider, model, '응답 기록 있음', 'ok')
    assert history[0]['finished'] >= history[0]['started']
    with closing(D._open(runtime)) as db:
        assert db.execute('SELECT slot FROM runs').fetchone()[0] == slot.isoformat()


def test_native_reenabled_and_invalid_routes_cannot_launch(schedule):
    job, _, runtime = schedule
    with pytest.raises(ValueError, match='모델 ID'):
        D.set_route(runtime, TASK, 'Codex', '--bad')
    with pytest.raises(ValueError, match='앱에서 관리'):
        D.set_route(runtime, 'trading-preflight', 'Codex', 'gpt-6.2-sol')
    assert not D._path(runtime).exists()
    D.set_route(runtime, TASK, 'Codex', 'gpt-6.2-sol')
    route = D.route_rows(runtime)[0][TASK]
    D._run(runtime, route, datetime(2030, 4, 1, 9, 20, tzinfo=KST))
    assert D.route_history(runtime, TASK) == []
