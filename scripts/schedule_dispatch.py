"""Opt-in Claude/Codex execution of existing read-only schedules."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from schedules import KINDS, KST, REPO, claude_jobs, codex_jobs, starts_on
from state_sync import runtime_root

SERVICE = 'ai-schedule-runner'
OPTIONS = {
    'Claude': ('claude-sonnet-5-5', 'claude-opus-5-5', 'claude-haiku-5-5'),
    'Codex': ('gpt-6.2-sol', 'gpt-6.2-astra', 'gpt-6.1-sol'),
}
# Jobs that can complete with reports/scratch writes and the read-only project CLI.
SHARED = 'shared-etf-paper/reviews'
LONGTERM = 'vibe-longterm/reviews'
TARGETS = {
    'trading-intraday-report': (),
    'trading-close-report': (),
    'trading-daily-improvement': (SHARED,),
    'premarket-analysis-daily': (SHARED,),
    'intraday-audit-hourly': (SHARED,),
    'longterm-ops-audit-daily': (LONGTERM,),
    'longterm-review-daily': (LONGTERM,),
    'longterm-audit-weekly': (LONGTERM,),
    'longterm-audit-monthly': (LONGTERM,),
    'longterm-strategy-daily': ('vibe-longterm/reports',),
    'longterm-strategy-monthly': ('vibe-longterm/reports',),
}
CODEX_EQUIVALENTS = {
    'premarket-analysis-daily': 'automation-2',
    'intraday-audit-hourly': 'automation',
    'longterm-ops-audit-daily': 'automation-3',
}
TIMEOUT = 12 * 60
MODEL_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')


def _path(runtime):
    return runtime_root(runtime) / 'nhplug' / 'scheduled-ai.sqlite3'


def _open(runtime, write=False):
    path = _path(runtime)
    if not write:
        if not path.is_file():
            raise FileNotFoundError(path)
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
        db = sqlite3.connect(path, timeout=3)
        db.executescript("""
            CREATE TABLE IF NOT EXISTS routes (
                task TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
                cron TEXT NOT NULL, saved TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                task TEXT NOT NULL, slot TEXT NOT NULL, started TEXT NOT NULL, finished TEXT,
                provider TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, result TEXT, resolved_model TEXT,
                PRIMARY KEY (task, slot)
            );
        """)
    db.row_factory = sqlite3.Row
    return db


def _skill(key, home=None):
    return Path(home or Path.home()) / '.claude/scheduled-tasks' / key / 'SKILL.md'


def eligibility(job):
    if job.get('route'):
        return ''
    if job['kind'] != KINDS[1]:
        return ('시스템 서비스는 등록된 실행기를 사용합니다.' if job['kind'] == KINDS[0]
                else 'Codex 앱에서 만든 작업은 Codex 자동화 설정에서 모델을 변경할 수 있습니다.')
    if not job.get('native'):
        return '앱의 현재 등록 상태를 확인할 수 없는 작업입니다.'
    if job['key'] not in TARGETS or not job.get('cron'):
        return '현재 앱에서 실행하는 작업입니다. 운영 변경·별도 데이터 저장·일회성 작업은 앱에서 관리합니다.'
    return ''


def executable(provider):
    command = {'Claude': 'claude', 'Codex': 'codex'}[provider]
    home = Path.home()
    paths = (home / '.claude/local/claude', home / '.local/bin' / command, Path('/opt/homebrew/bin') / command)
    return shutil.which(command) or next((str(p) for p in paths
                                          if p.name == command and p.is_file() and os.access(p, os.X_OK)), None)


def runner_ready():
    from os_services import MISSING, Unknown, state

    try:
        return state(SERVICE).get('registered', MISSING['registered'])
    except Unknown:
        return False


def _block(job, route, others):
    if not job or not job.get('native'):
        return 'Claude 앱에서 현재 작업 등록을 확인할 수 없습니다.'
    if job.get('enabled') is not False:
        return '중복 실행을 막기 위해 Claude 앱에서 이 작업을 먼저 일시 정지하세요.'
    if job.get('cron') != route['cron'] or not job.get('times'):
        return '원본 일정이 바뀌었습니다. AI 설정을 다시 저장하세요.'
    equivalent = CODEX_EQUIVALENTS.get(job['key'])
    if equivalent and any(j['key'] == equivalent and j['state'] != '일시 정지' for j in others):
        return '같은 일을 하는 Codex 자동화를 일시 정지하세요.'
    return ''


def set_route(runtime, key, provider, model):
    """Persist only dispatch settings; a native app is never modified."""
    if key not in TARGETS:
        raise ValueError('이 작업은 앱에서 관리합니다.')
    if not provider:
        if _path(runtime).is_file():
            with closing(_open(runtime, write=True)) as db, db:
                db.execute('DELETE FROM routes WHERE task=?', (key,))
        return
    model = str(model or '').strip()
    if provider not in OPTIONS or not MODEL_ID.fullmatch(model):
        raise ValueError('실행 AI와 올바른 모델 ID를 입력하세요.')
    job = next((j for j in claude_jobs() if j['key'] == key), None)
    cron = job.get('cron') if job else None
    # A route can be saved while the original job still runs. It stays inactive until the original is paused.
    if reason := _block({**job, 'enabled': False} if job else None, {'cron': cron}, []):
        raise ValueError(reason)
    if not runner_ready():
        raise ValueError('이 Mac의 예약 AI 실행기가 등록되어 있어야 합니다.')
    if not executable(provider):
        raise ValueError(f'{provider} CLI를 찾을 수 없습니다.')
    with closing(_open(runtime, write=True)) as db, db:
        db.execute('INSERT OR REPLACE INTO routes VALUES(?,?,?,?,?)',
                   (key, provider, model, cron, datetime.now(KST).isoformat()))


def _record(row):
    started = datetime.fromisoformat(row['started'])
    status = row['status']
    if status == '실행 중' and (datetime.now(KST) - started).total_seconds() > TIMEOUT + 90:
        status = '완료 기록 없음'
    return {'started': started,
            'finished': datetime.fromisoformat(row['finished']) if row['finished'] else None,
            'status': status, 'model': row['resolved_model'] or f"{row['model']} (요청)", 'provider': row['provider'],
            'source': '대시보드 예약 실행기', 'result': row['result']}


def route_rows(runtime, jobs=None, codex=None):
    try:
        with closing(_open(runtime)) as db:
            routes = {r['task']: dict(r) for r in db.execute('SELECT * FROM routes')}
            latest = {r['task']: [_record(r)] for r in db.execute(
                'SELECT * FROM runs WHERE (task, slot) IN (SELECT task, max(slot) FROM runs GROUP BY task)')}
    except (FileNotFoundError, sqlite3.Error):
        return {}, {}
    if routes:
        registered = runner_ready()
        jobs = {j['key']: j for j in (jobs if jobs is not None else claude_jobs())}
        others = codex if codex is not None else codex_jobs()
        for key, route in routes.items():
            reason = (_block(jobs.get(key), route, others) or
                      ('예약 AI 실행기 미등록' if not registered else '') or
                      ('CLI를 찾을 수 없음' if not executable(route['provider']) else ''))
            route.update(ready=not reason, reason=reason)
    return routes, latest


def route_history(runtime, key):
    try:
        with closing(_open(runtime)) as db:
            return [_record(r) for r in db.execute('SELECT * FROM runs WHERE task=? ORDER BY slot DESC', (key,))]
    except (FileNotFoundError, sqlite3.Error):
        return []


def _due(runtime, now):
    try:
        with closing(_open(runtime)) as db:
            routes = {row['task']: dict(row) for row in db.execute('SELECT * FROM routes')}
    except FileNotFoundError:
        return []
    if not routes:
        return []
    others = codex_jobs()
    slot = now.replace(second=0, microsecond=0)
    return [(route, slot) for job in claude_jobs() if (route := routes.get(job['key'])) and
            job['key'] in TARGETS and not _block(job, route, others) and
            slot > datetime.fromisoformat(route['saved']) and
            (slot.hour, slot.minute) in starts_on({**job, 'state': '등록됨'}, slot.date())]


def _claim(runtime, route, slot):
    now = datetime.now(KST)
    with closing(_open(runtime, write=True)) as db, db:
        db.execute("UPDATE runs SET status='중단', finished=? WHERE status='실행 중' AND started < ?",
                   (now.isoformat(), (now - timedelta(seconds=TIMEOUT + 90)).isoformat()))
        claimed = db.execute("""
            INSERT OR IGNORE INTO runs (task, slot, started, provider, model, status)
            SELECT task, ?, ?, provider, model, '실행 중' FROM routes
            WHERE task=? AND saved=? AND NOT EXISTS
                (SELECT 1 FROM runs WHERE task=? AND status='실행 중')
        """, (slot.isoformat(), now.isoformat(), route['task'], route['saved'], route['task']))
        return claimed.rowcount == 1


def _commands(provider, binary, model, work, roots, scratch):
    if provider == 'Codex':
        return [binary, 'exec', '-C', str(work), '-m', model, '-s', 'workspace-write', '--ignore-user-config',
                '--ignore-rules', '--skip-git-repo-check', '--ephemeral', '--json', '-c', 'approval_policy="never"',
                '-c', 'web_search="live"', *[arg for root in [scratch, *roots] for arg in ('--add-dir', str(root))], '-']
    protected = [str(REPO / '.env'), str(Path.home() / '.vibe-trading/.env')]
    settings = {'permissions': {
        'allow': ['Bash(scripts/ro *)', 'Bash(./scripts/ro *)',
                  *[f'{tool}(//{str(root).lstrip("/")}/**)' for root in [scratch, *roots] for tool in ('Write', 'Edit')]],
        'deny': ['Bash(scripts/ro pytest *)', 'Bash(./scripts/ro pytest *)',
                 *[f'Read(//{path.lstrip("/")})' for path in protected]],
    }, 'sandbox': {
        'enabled': True, 'failIfUnavailable': True, 'allowUnsandboxedCommands': False, 'autoAllowBashIfSandboxed': False,
        'filesystem': {'allowWrite': [str(root) for root in [scratch, *roots]], 'denyRead': protected},
        'network': {'allowedDomains': []},
    }}
    return [binary, '-p', '--restricted', '--safe-mode', '--permission-mode', 'dontAsk', '--settings', json.dumps(settings),
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--tools', 'Bash,Read,Glob,Grep,Write,Edit,WebSearch,WebFetch', '--output-format', 'json',
            '--model', model, *[arg for root in roots for arg in ('--add-dir', str(root))]]


def _response(provider, output):
    if provider == 'Claude':
        payload = json.loads(output)
        text = payload.get('result')
        if payload.get('is_error') or not isinstance(text, str) or not text.strip():
            return None, None
        models = payload.get('modelUsage')
        actual = next(iter(models)) if isinstance(models, dict) and len(models) == 1 else None
        return text, actual
    messages, complete, actual = [], False, None
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'turn.completed':
            complete = True
        if isinstance(event.get('model'), str):
            actual = event['model']
        item = event.get('item') or {}
        if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
            messages.append(item.get('text', ''))
    return (messages[-1], actual) if complete and messages and messages[-1].strip() else (None, None)


def _prompt(key, slot, skill):
    return (
        f'예약 분석 작업 {key}, 예약 시각 {slot:%Y-%m-%d %H:%M} KST. 저장소 {REPO}.\n'
        '아래 작업을 실행해 결과를 한국어로 보고하세요. 증권사·주문 기능, 서비스 조작, 매매 설정과 소스 변경, '
        '비밀값과 계좌번호의 열람·출력, 연구 DB 갱신, 외부 발송은 허용되지 않습니다. 원본에 이런 절차가 있으면 '
        '해당 단계는 건너뛰고 누락을 적으세요. 셸은 저장소의 scripts/ro 조회와 리포트 작성에만 사용하세요. '
        '도구가 막히거나 작성이 끝나지 않으면 완료로 보고하지 말고 어느 단계가 미완료인지 적으세요.\n\n' + skill
    )


def _run(runtime, route, slot):
    key, provider, model = (route[k] for k in ('task', 'provider', 'model'))
    # Re-read app state before claiming, including edits made after this tick selected the job.
    from state_sync import app_stores, last_runs

    job = next((j for j in claude_jobs() if j['key'] == key), None)
    previous_native = last_runs(app_stores()).get(key)
    if (_block(job, route, codex_jobs()) or (previous_native and previous_native >= slot)
            or not _claim(runtime, route, slot)):
        return
    status, result, actual = '실패', None, None
    try:
        skill = _skill(key).read_text(encoding='utf-8')
        binary = executable(provider)
        if not binary:
            raise FileNotFoundError
        root = runtime_root(runtime)
        scratch = REPO / '.scratch'
        scratch.mkdir(exist_ok=True, mode=0o700)
        roots = [root / 'nhplug' / relative for relative in TARGETS[key]]
        for folder in roots:
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='scheduled-ai-') as work, tempfile.TemporaryFile() as stdout:
            args = _commands(provider, binary, model, work, roots, scratch)
            safe_env = {name: os.environ[name] for name in ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR')
                        if name in os.environ}
            safe_env['VIBE_TRADING_HOME'] = str(root)
            with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=stdout, stderr=subprocess.DEVNULL, text=True,
                                  cwd=REPO if provider == 'Claude' else work, env=safe_env, start_new_session=True) as proc:
                try:
                    proc.communicate(_prompt(key, slot, skill), timeout=TIMEOUT)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    raise
                if proc.returncode == 0:
                    stdout.seek(0)
                    result, actual = _response(provider, stdout.read().decode('utf-8', 'replace'))
                    if result:
                        status = '응답 기록 있음'
                else:
                    result = f'CLI 종료 코드 {proc.returncode}'
        if not result:
            result = '완료 응답을 읽을 수 없습니다.'
    except subprocess.TimeoutExpired:
        status, result = '시간 초과', '예약 AI 응답 제한 시간 초과'
    except (OSError, ValueError, TypeError):
        result = '작업 지시문·CLI 또는 실행 응답을 확인할 수 없습니다.'
    from report_pages import mask
    result = mask(re.sub(r'https://[^\s)>]+/api/webhooks/[^\s)>]+|\b(?:sk-ant-|sk-|ghp_)[A-Za-z0-9_-]{12,}', '••••', result))
    with closing(_open(runtime, write=True)) as db, db:
        db.execute('UPDATE runs SET status=?, finished=?, result=?, resolved_model=? WHERE task=? AND slot=?',
                   (status, datetime.now(KST).isoformat(), result, actual, key, slot.isoformat()))


def watch(runtime):
    queued = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            for identity, work in list(queued.items()):
                if work.done():
                    try:
                        work.result()
                    except (OSError, sqlite3.Error) as exc:
                        print(f'{identity[0]}: {type(exc).__name__}', flush=True)
                    del queued[identity]
            try:
                for route, slot in _due(runtime, datetime.now(KST)):
                    identity = (route['task'], slot)
                    if identity not in queued:
                        queued[identity] = pool.submit(_run, runtime, route, slot)
            except (OSError, sqlite3.Error) as exc:
                print(f'예약 확인: {type(exc).__name__}', flush=True)
            time.sleep(15)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime')
    watch(parser.parse_args().runtime)
