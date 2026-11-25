import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import schedule_history as H


def test_claude_session_is_scoped_and_uses_actual_model(tmp_path):
    store = tmp_path / 'Library/Application Support/Claude/claude-code-sessions/a/b'
    store.mkdir(parents=True)
    (store / 'scheduled-tasks.json').write_text(json.dumps({'scheduledTasks': [
        {'id': 'audit', 'cwd': str(H.REPO), 'lastRunAt': '2026-11-25T00:20:00Z'},
    ]}))
    session = {'scheduledTaskId': 'audit', 'cwd': str(H.REPO), 'createdAt': 1795566000000,
               'lastActivityAt': 1795566100000, 'model': 'actual-model', 'completedTurns': 1}
    (store / 'one.json').write_text(json.dumps(session))
    (store / 'foreign.json').write_text(json.dumps({**session, 'cwd': '/another/project', 'model': 'wrong'}))
    skill = tmp_path / '.claude/scheduled-tasks/audit/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: audit\nmodel: configured-model\n---\nBody')
    assert H.claude_declared_model('audit', tmp_path) == 'configured-model'
    assert H.claude_declared_model('../audit', tmp_path) is None
    rows = H.native_history([{'kind': 'Claude 예약 작업', 'key': 'audit'}], tmp_path, detailed=True)[('Claude 예약 작업', 'audit')]
    assert len(rows) == 1
    assert rows[0]['model'] == 'actual-model'
    assert rows[0]['provider'] == 'Claude'
    assert rows[0]['status'] == '응답 기록 있음'
    assert rows[0]['started'] == datetime(2026, 11, 25, 9, 20, tzinfo=ZoneInfo('Asia/Seoul'))


def test_codex_uses_retained_run_and_reports_fallback_start(tmp_path):
    folder = tmp_path / '.codex/sqlite'
    folder.mkdir(parents=True)
    with sqlite3.connect(folder / 'codex.db') as db:
        db.execute('CREATE TABLE automation_runs (automation_id, thread_id, status, created_at, updated_at)')
        db.execute('CREATE TABLE automations (id, last_run_at)')
        db.execute('INSERT INTO automation_runs VALUES (?, ?, ?, ?, ?)', ('audit', 'thread', 'ACCEPTED', 1795566000000, 1795566010000))
        db.execute('INSERT INTO automations VALUES (?, ?)', ('audit', 1795566000000))
        db.execute('INSERT INTO automations VALUES (?, ?)', ('other', 1795566000000))
    with sqlite3.connect(folder / 'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads (id, model)')
        db.execute('INSERT INTO threads VALUES (?, ?)', ('thread', 'gpt-test'))
    jobs = [{'kind': 'Codex 자동화', 'key': key} for key in ('audit', 'other')]
    rows = H.native_history(jobs, tmp_path, detailed=True)
    assert len(rows[('Codex 자동화', 'audit')]) == 1
    assert rows[('Codex 자동화', 'audit')][0]['model'] == 'gpt-test'
    assert rows[('Codex 자동화', 'audit')][0]['provider'] == 'Codex'
    assert rows[('Codex 자동화', 'other')][0]['source'] == 'Codex 예약 작업'


def test_service_log_observation_is_not_a_start(tmp_path):
    log = tmp_path / 'nhplug/shared-etf-paper/com.vibetrading.premarket.log'
    log.parent.mkdir(parents=True)
    log.write_text('{"time": "2026-11-25T09:10:04+09:00", "status": "ok"}\n')
    job = {'kind': 'launchd 서비스', 'key': 'com.vibetrading.premarket'}
    row = H.native_history([job], tmp_path, runtime=tmp_path / 'nhplug')[('launchd 서비스', job['key'])][0]
    assert row['started'] is None
    assert row['observed'].hour == 9
    assert row['source'] == '서비스 로그'


def test_nxt_session_starts_and_stops_are_paired_without_inventing_missing_stop(tmp_path):
    log = tmp_path / 'nhplug/nxt-virtual/events.jsonl'
    log.parent.mkdir(parents=True)
    log.write_text('\n'.join(json.dumps(row) for row in [
        {'time': '2026-11-24T15:35:01+09:00', 'event': 'nxt_started', 'session': 'evening'},
        {'time': '2026-11-24T19:58:00+09:00', 'event': 'nxt_stopped', 'session': 'evening'},
        {'time': '2026-11-25T07:55:01+09:00', 'event': 'nxt_started', 'session': 'morning'},
    ]))
    job = {'kind': 'launchd 서비스', 'key': 'com.vibetrading.nxt-virtual'}
    rows = H.native_history([job], tmp_path, runtime=tmp_path / 'nhplug', detailed=True)[('launchd 서비스', job['key'])]
    assert len(rows) == 2
    assert rows[0]['finished'] is None and rows[0]['status'] == '시작 기록'
    assert rows[1]['finished'].hour == 19 and rows[1]['status'] == '종료 기록'


def test_preflight_includes_all_retained_days_as_check_observations(tmp_path):
    folder = tmp_path / 'nhplug/preflight'
    folder.mkdir(parents=True)
    for day, ok in ((24, False), (25, True)):
        (folder / f'2026-11-{day}.jsonl').write_text(json.dumps({
            'time': f'2026-11-{day}T08:20:00+09:00', 'results': [], 'ok': ok,
        }))
    job = {'kind': 'launchd 서비스', 'key': 'com.vibetrading.preflight'}
    rows = H.native_history([job], tmp_path, runtime=tmp_path / 'nhplug', detailed=True)[('launchd 서비스', job['key'])]
    assert [row['status'] for row in rows] == ['점검 통과', '점검 실패']
    assert all(row['started'] is None and row['observed'] for row in rows)
