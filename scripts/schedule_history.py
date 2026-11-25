"""Read retained scheduler records without modifying the schedulers or starting jobs."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
REPO = Path(__file__).resolve().parents[1]


def _time(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 100_000_000_000 else value, KST)
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed.astimezone(KST)
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def _json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        return None


def claude_declared_model(task, home=None):
    """Return a model declared in the task skill, which may differ from the model actually used."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', str(task)):
        return None
    skill = Path(home or Path.home()) / '.claude' / 'scheduled-tasks' / task / 'SKILL.md'
    try:
        text = skill.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None
    if not text.startswith('---\n'):
        return None
    front = text.split('---', 2)[1]
    match = re.search(r'^model:\s*([^\n#]+)', front, re.M)
    return match.group(1).strip().strip('"\'') if match else None


def _claude(jobs, home):
    base = home / 'Library/Application Support/Claude/claude-code-sessions'
    stores = sorted(base.glob('*/*/scheduled-tasks.json'), key=lambda p: p.stat().st_mtime)
    if not stores:
        return {}
    store = None
    for path in reversed(stores):
        data = _json(path)
        if isinstance(data, dict) and isinstance(data.get('scheduledTasks'), list):
            store = path
            break
    if store is None:
        return {}
    wanted = {job['key'] for job in jobs if job['kind'] == 'Claude 예약 작업'}
    rows = {key: [] for key in wanted}
    for path in store.parent.glob('*.json'):
        if path == store:
            continue
        data = _json(path)
        if not isinstance(data, dict):
            continue
        key = data.get('scheduledTaskId')
        cwd = data.get('originCwd') or data.get('cwd')
        if key not in wanted or not cwd or str(cwd).rstrip('/') != str(REPO).rstrip('/'):
            continue
        started = _time(data.get('createdAt'))
        if not started:
            continue
        status = ('오류 기록' if data.get('errorAt') or data.get('error') else
                  '중단 기록' if data.get('lastTurnCutShort') or data.get('interruptedAwaitingPerson') else
                  '응답 기록 있음' if data.get('completedTurns') else '시작 기록')
        rows[key].append({'started': started, 'finished': None, 'status': status,
                          'model': data.get('model') or None, 'provider': 'Claude', 'source': 'Claude 세션',
                          'observed': _time(data.get('lastActivityAt')), 'session_id': data.get('sessionId')})
    for task in (_json(store) or {}).get('scheduledTasks', []):
        if not isinstance(task, dict):
            continue
        key = task.get('id')
        if key not in wanted or (task.get('cwd') and str(task['cwd']).rstrip('/') != str(REPO)):
            continue
        started = _time(task.get('lastRunAt'))
        # A retained session and the scheduler's last-run field can differ by a few seconds.
        if started and not any(abs((r['started'] - started).total_seconds()) < 120 for r in rows[key]):
            rows[key].append({'started': started, 'finished': None, 'status': '시작 기록',
                              'model': None, 'provider': 'Claude', 'source': 'Claude 예약 작업', 'observed': None})
    return {('Claude 예약 작업', k): v for k, v in rows.items()}


def _db(path):
    if not path.is_file():
        return None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        return db
    except sqlite3.Error:
        return None


def _codex(jobs, home):
    wanted = {job['key'] for job in jobs if job['kind'] == 'Codex 자동화'}
    rows = {key: [] for key in wanted}
    db = _db(home / '.codex/sqlite/codex.db')
    if db is None:
        return {('Codex 자동화', k): v for k, v in rows.items()}
    try:
        runs = list(db.execute('SELECT automation_id, thread_id, status, created_at, updated_at FROM automation_runs'))
        last = list(db.execute('SELECT id, last_run_at FROM automations'))
    except sqlite3.Error:
        return {('Codex 자동화', k): v for k, v in rows.items()}
    finally:
        db.close()
    thread_models = {}
    state = _db(home / '.codex/sqlite/state_5.sqlite')
    if state:
        try:
            thread_models = {r['id']: r['model'] for r in state.execute('SELECT id, model FROM threads')}
        except sqlite3.Error:
            pass
        finally:
            state.close()
    for run in runs:
        key = run['automation_id']
        if key not in wanted:
            continue
        rows[key].append({'started': _time(run['created_at']), 'finished': None, 'status': run['status'] or '상태 확인 불가',
                          'model': thread_models.get(run['thread_id']), 'provider': 'Codex', 'source': 'Codex 실행 기록',
                          'observed': _time(run['updated_at']), 'session_id': run['thread_id']})
    for automation in last:
        key = automation['id']
        if key not in wanted:
            continue
        started = _time(automation['last_run_at'])
        if started and not any(r['started'] and abs((r['started'] - started).total_seconds()) < 120 for r in rows[key]):
            rows[key].append({'started': started, 'finished': None, 'status': '시작 기록', 'model': None,
                              'provider': 'Codex', 'source': 'Codex 예약 작업', 'observed': None})
    return {('Codex 자동화', k): v for k, v in rows.items()}


def _lines(path, detailed):
    try:
        with path.open('rb') as stream:
            if not detailed:
                stream.seek(max(0, path.stat().st_size - 256_000))
            for line in stream:
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _service_records(name, runtime, detailed):
    rows = []
    if name == 'nxt-virtual':
        active = {}
        for entry in _lines(runtime / 'nxt-virtual/events.jsonl', detailed):
            at, event, session = _time(entry.get('time')), entry.get('event'), entry.get('session')
            if not at or not session:
                continue
            if event == 'nxt_started':
                row = {'started': at, 'finished': None, 'status': '시작 기록', 'model': None,
                       'provider': None, 'source': 'NXT 세션 기록', 'observed': None, 'session': session}
                rows.append(row)
                active[session] = row
            elif event == 'nxt_stopped' and session in active:
                row = active.pop(session)
                row['finished'] = at
                row['status'] = '종료 기록'
        return rows
    if name == 'preflight':
        files = sorted((runtime / 'preflight').glob('*.jsonl'))
        if not detailed:
            files = files[-5:]
        for file in files:
            for entry in _lines(file, detailed):
                at = _time(entry.get('time'))
                if at and isinstance(entry.get('results'), list):
                    status = '점검 통과' if entry.get('ok') is True else '점검 실패' if entry.get('ok') is False else '점검 기록'
                    rows.append({'started': None, 'finished': None, 'status': status, 'model': None,
                                 'provider': None, 'source': '장전 점검 기록', 'observed': at})
        return rows
    if name == 'trading-day-start':
        path = runtime / 'shared-etf-paper/trading-day-start.jsonl'
        for entry in _lines(path, detailed):
            at = _time(entry.get('time'))
            if at and entry.get('decision'):
                rows.append({'started': None, 'finished': None, 'status': '준비 판단 기록', 'model': None,
                             'provider': None, 'source': '거래일 준비 기록', 'observed': at})
        return rows
    if name == 'premarket':
        path = runtime / 'shared-etf-paper/com.vibetrading.premarket.log'
        for entry in _lines(path, detailed):
            at = _time(entry.get('time'))
            if at and isinstance(entry.get('sections'), dict):
                rows.append({'started': None, 'finished': None, 'status': '수집 기록', 'model': None,
                             'provider': None, 'source': '장전 수집 로그', 'observed': at})
    return rows


def _launchd(jobs, home, runtime, detailed):
    import launchd_services as services

    result = {}
    for job in jobs:
        if job['kind'] != 'launchd 서비스':
            continue
        name = job['key'].removeprefix('com.vibetrading.')
        if name not in services.SERVICES:
            continue
        folder = services.SERVICES[name][1]
        log = Path(runtime) / folder / ('streamlit.log' if name == 'shared-etf-dashboard' else f'{job["key"]}.log')
        rows = _service_records(name, runtime, detailed)
        if rows:
            result[(job['kind'], job['key'])] = rows
            continue
        try:
            with log.open('rb') as stream:
                stream.seek(max(0, log.stat().st_size - 256_000))
                for line in reversed(stream.read().decode('utf-8', 'replace').splitlines()):
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    observed = _time(entry.get('time'))
                    if observed:
                        rows.append({'started': None, 'finished': None, 'status': '로그 관측', 'model': None,
                                     'provider': None, 'source': '서비스 로그', 'observed': observed})
                        break
        except OSError:
            pass
        result[(job['kind'], job['key'])] = rows
    return result


def native_history(jobs, home=None, runtime=None, detailed=False):
    """Return newest-first retained records indexed by (kind, key), with KST datetime values.

    A service log observation is explicitly distinguished from a scheduler start. Overview reads
    only the newest 20 entries per task, while detailed mode returns every retained session row.
    """
    home = Path(home or Path.home())
    runtime = Path(runtime or home / '.vibe-trading/nhplug')
    records = {}
    records.update(_claude(jobs, home))
    records.update(_codex(jobs, home))
    records.update(_launchd(jobs, home, runtime, detailed))
    for key, values in records.items():
        values.sort(key=lambda r: r['started'] or r.get('observed') or datetime.min.replace(tzinfo=KST), reverse=True)
        if not detailed:
            records[key] = values[:20]
    return records
