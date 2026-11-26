"""Plan and apply changes to project-owned reservation execution routes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import select
import subprocess
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

import schedule_dispatch as dispatch
from schedules import KINDS, KST, claude_jobs, codex_jobs
from state_sync import runtime_root

PROVIDERS = {'claude': 'Claude', 'codex': 'Codex', 'antigravity': 'Antigravity'}
PROFILE = re.compile(r'[a-z0-9][a-z0-9_.-]{0,39}')
PLAN_ID = re.compile(r'schedule-[a-f0-9]{12}')


def _db(runtime, write=False):
    db = dispatch._open(runtime, write=write)
    if write:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS schedule_profiles (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS schedule_plans (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS schedule_bindings (task TEXT PRIMARY KEY, saved TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS schedule_imports (task TEXT PRIMARY KEY, data TEXT NOT NULL);
        """)
    return db


def _lookup(db, table, key, column='id'):
    # Only used with fixed table/column names in this module.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return None
    row = db.execute(f'SELECT data FROM {table} WHERE {column}=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _env(profile):
    if profile['provider'] == 'Antigravity':
        from schedule_antigravity import env
        return env(profile)
    env = {k: os.environ[k] for k in ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
    config = profile.get('config_dir')
    if config:
        key = 'CLAUDE_CONFIG_DIR' if profile['provider'] == 'Claude' else 'CODEX_HOME'
        env[key] = config
    return env


def _account(profile):
    """Identify a provider login without emitting account identifiers or reading secrets into the plan."""
    binary = dispatch.executable(profile['provider'])
    if not binary:
        raise ValueError('대상 CLI를 설치해야 합니다.')
    if profile['provider'] == 'Antigravity':
        from schedule_antigravity import account
        return account(profile)
    env = _env(profile)
    try:
        if profile['provider'] == 'Claude':
            out = subprocess.run([binary, 'auth', 'status', '--json'], capture_output=True, text=True, env=env, timeout=8)
            info = json.loads(out.stdout)
            account = info.get('email') or info.get('userId')
            if out.returncode or not info.get('loggedIn') or not account:
                raise ValueError('Claude CLI에서 로그인 계정을 확인할 수 없습니다.')
            identity = (account, info.get('orgId'), info.get('authMethod'))
        elif profile['provider'] == 'Codex':
            # The documented app-server account/read method exposes account identity without tokens.
            request = [
                {'id': 0, 'method': 'initialize', 'params': {'clientInfo': {'name': 'omni-schedules', 'version': '1'}}},
                {'method': 'initialized'},
                {'id': 1, 'method': 'account/read', 'params': {'refreshToken': False}},
            ]
            account = _codex_account(binary, env, request)
            if not isinstance(account, dict) or not account.get('email'):
                raise ValueError('Codex CLI에서 로그인 계정을 확인할 수 없습니다.')
            identity = (account['email'], account.get('type'))
        else:
            raise ValueError('지원하지 않는 예약 실행기입니다.')
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, AttributeError) as exc:
        raise ValueError('CLI 계정 확인에 실패했습니다.') from exc
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _codex_account(binary, env, request):
    import time

    with subprocess.Popen([binary, 'app-server', '--listen', 'stdio://'], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env) as proc:
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(('\n'.join(map(json.dumps, request)) + '\n').encode())
            proc.stdin.flush()
            deadline, pending = time.monotonic() + 8, b''
            while (remaining := deadline - time.monotonic()) > 0 and select.select([proc.stdout], [], [], remaining)[0]:
                chunk = os.read(proc.stdout.fileno(), 8192)
                if not chunk:
                    break
                lines = (pending + chunk).split(b'\n')
                pending = lines.pop()
                for line in lines:
                    data = json.loads(line)
                    if data.get('id') == 1:
                        return data.get('result', {}).get('account')
        finally:
            proc.kill()
    return None


def _public(profile):
    return {k: v for k, v in profile.items() if k not in ('fingerprint', 'config_dir', 'credential_file')}


def profiles(runtime):
    try:
        with closing(_db(runtime)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='schedule_profiles'").fetchone():
                return []
            return [_public(json.loads(row['data'])) for row in db.execute('SELECT data FROM schedule_profiles ORDER BY id')]
    except FileNotFoundError:
        return []


def register_profile(runtime, provider, alias, model, config_dir=None, credential_file=None):
    if provider not in PROVIDERS or not PROFILE.fullmatch(alias) or not dispatch.MODEL_ID.fullmatch(model):
        raise ValueError('제공자, 계정 별칭 또는 모델 ID가 올바르지 않습니다.')
    if config_dir and provider == 'antigravity':
        raise ValueError('Antigravity 인증에는 --credential-file을 사용하세요.')
    if credential_file and provider != 'antigravity':
        raise ValueError('--credential-file은 Antigravity 프로필에 사용합니다.')
    config = Path(config_dir).expanduser().resolve(strict=True) if config_dir else None
    if config and not config.is_dir():
        raise ValueError('인증 설정 경로가 폴더가 아닙니다.')
    profile = {'id': f'{provider}:{alias}', 'provider': PROVIDERS[provider], 'model': model,
               'config_dir': str(config) if config else None, 'credential_file': str(credential_file) if credential_file else None,
               'status': '인증 확인'}
    profile['fingerprint'] = _account(profile)
    with closing(_db(runtime, write=True)) as db, db:
        old = _lookup(db, 'schedule_profiles', profile['id'])
        if old and old['fingerprint'] != profile['fingerprint']:
            raise ValueError('이 별칭은 다른 계정에 연결되어 있습니다. 새 별칭을 사용하세요.')
        if old and old.get('probe') and old == profile | {'probe': old['probe']}:
            profile['probe'] = old['probe']
        db.execute('INSERT OR REPLACE INTO schedule_profiles VALUES (?, ?)', (profile['id'], json.dumps(profile)))
    return _public(profile)


def _profile(runtime, target):
    try:
        with closing(_db(runtime)) as db:
            profile = _lookup(db, 'schedule_profiles', target)
    except FileNotFoundError:
        profile = None
    if not profile:
        raise ValueError('대상 실행 프로필을 먼저 등록하세요.')
    return profile


def probe(runtime, target):
    """Run a synthetic model check without project files, order data, or report writes."""
    profile = _profile(runtime, target)
    response, used = _test_model(profile, 'Use no tools. Calculate 12 + 7 - 3. Output only OMNI-CHECK:<number> with your calculated number.')
    model = profile['model']
    ok = response is not None and response.strip() == 'OMNI-CHECK:16' and (used is None or used == model)
    profile['probe'] = {'ok': ok, 'model': model, 'used_model': used, 'at': datetime.now(KST).isoformat()}
    with closing(_db(runtime, write=True)) as db, db:
        current = _lookup(db, 'schedule_profiles', target)
        if not current or current | {'probe': None} != profile | {'probe': None}:
            raise ValueError('검증 중 프로필이 변경됐습니다.')
        db.execute('UPDATE schedule_profiles SET data=? WHERE id=?', (json.dumps(profile), target))
    return _public(profile)


def _test_model(profile, prompt):
    import tempfile

    if profile['fingerprint'] != _account(profile):
        raise ValueError('대상 CLI 로그인 계정이 등록할 때와 다릅니다.')
    provider, model = profile['provider'], profile['model']
    with tempfile.TemporaryDirectory(prefix='omni-model-check-') as tmp:
        work = Path(tmp)
        binary = dispatch.executable(provider)
        if provider == 'Antigravity':
            from schedule_antigravity import test_model
            return test_model(profile, binary, prompt, work)
        args = dispatch._commands(provider, binary, model, work, [], work)
        try:
            out = subprocess.run(args, input=prompt, capture_output=True, text=True, cwd=work,
                                 env=_env(profile), timeout=60)
            response, used = dispatch._response(provider, out.stdout) if out.returncode == 0 else (None, None)
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
            response, used = None, None
    return response, used


def validate_model(runtime, identifier, job_id, fixture):
    """Compare a candidate against expected structured outcomes on supplied safe test data."""
    from schedule_bundle import SECRET

    plan = _plan(runtime, identifier)
    row = next((r for r in plan['rows'] if r['id'] == job_id), None)
    if plan['state'] != '계획' or not row or row['status'] != '대체 모델 검증 필요':
        raise ValueError('대체 모델 검증이 필요한 작업을 선택하세요.')
    text = Path(fixture).read_text(encoding='utf-8')
    cases = json.loads(text)
    if (len(text) > 50_000 or SECRET.search(text) or not isinstance(cases, list) or not 2 <= len(cases) <= 10
            or any(not isinstance(c, dict) or not isinstance(c.get('input'), dict)
                   or not isinstance(c.get('expected'), dict) or not c['expected'] for c in cases)):
        raise ValueError('비식별 검증 자료는 input·expected 객체를 가진 2~10개 사례여야 합니다.')
    profile = _profile(runtime, plan['target'])
    if profile != plan['profile']:
        raise ValueError('대상 프로필이 변경됐습니다.')
    definition = plan.get('imports', {}).get(row['key'])
    prompt = definition['prompt'] if definition else raw_prompt(runtime, row['key'])
    if hashlib.sha256(prompt.encode()).hexdigest() != row['prompt_sha256']:
        raise ValueError('원본 지시문이 변경됐습니다.')
    passed = 0
    for case in cases:
        request = ('Read the following job instructions as reference. Use no tools and make no writes. '
                   'Use only the supplied synthetic fixture and return the required result as one JSON object.\n'
                   + prompt + '\nFixture:\n' + json.dumps(case['input'], ensure_ascii=False))
        answer, used = _test_model(profile, request)
        try:
            valid = json.loads(answer or 'null') == case['expected']
        except ValueError:
            valid = False
        passed += int(valid and (used is None or used == profile['model']))
    row['validation'] = {'passed': passed, 'total': len(cases), 'fixture_sha256': hashlib.sha256(text.encode()).hexdigest()}
    if passed == len(cases):
        row.update(status='이전 가능', reason='검증 자료 통과. 원본 예약 일시 정지 후 적용합니다.')
    with closing(_db(runtime, write=True)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        saved = _lookup(db, 'schedule_plans', identifier)
        if saved['state'] != '계획':
            raise ValueError('검증 중 계획이 변경됐습니다.')
        saved['rows'] = [row if r['id'] == job_id else r for r in saved['rows']]
        _save(db, saved)
    return _public_plan(saved)


def _plan(runtime, identifier):
    if not PLAN_ID.fullmatch(identifier):
        raise ValueError('예약 이전 계획 ID가 올바르지 않습니다.')
    try:
        with closing(_db(runtime)) as db:
            result = _lookup(db, 'schedule_plans', identifier)
    except FileNotFoundError:
        result = None
    if not result:
        raise ValueError('예약 이전 계획을 찾을 수 없습니다.')
    return result


def _save(db, plan):
    db.execute('INSERT OR REPLACE INTO schedule_plans VALUES (?, ?)', (plan['id'], json.dumps(plan, ensure_ascii=False)))


def create_plan(runtime, target, bundle=None, jobs=(), receipt=None, source_key=None):
    from schedule_bundle import inventory, origin, validate_bundle
    from schedule_handoff import device_id, validate_receipt
    from schedule_imports import catalog, load
    from schedules import cron_job, job_next_start

    if receipt is not None and bundle is not None:
        raise ValueError('예약 목록과 인수 영수증은 각각 계획을 만드세요.')
    if receipt is not None:
        if not isinstance(source_key, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', source_key):
            raise ValueError('원본 기기에서 확인한 서명 지문이 필요합니다.')
        validate_receipt(receipt, device_id(runtime), source_key=source_key)
    profile = _profile(runtime, target)
    current = {job['id']: job for job in inventory(runtime)}
    source = receipt['jobs'] if receipt else validate_bundle(bundle)['jobs'] if bundle is not None else list(current.values())
    imported = load(runtime) if receipt else {}
    native = {job['key']: job for job in catalog(runtime, claude_jobs())} if receipt else {}
    wanted = set(jobs)
    if wanted - {job['id'] for job in source}:
        raise ValueError('목록에 없는 작업 ID가 있습니다.')
    rows = []
    imports = {}
    for job in source:
        if wanted and job['id'] not in wanted:
            continue
        locally = current.get(job['id'])
        row = {k: job.get(k) for k in ('id', 'key', 'name', 'when', 'prompt_sha256')}
        row['dependency_hashes'] = job.get('dependency_hashes', {})
        row['cron'] = job['schedule'].get('cron')
        row['next_run'] = job['schedule'].get('next_run')
        row['previous_saved'] = ((locally or {}).get('route') or {}).get('saved')
        row.update(source_model=job.get('model'), source_last_model=job.get('last_model'),
                   target_model=profile['model'], status='이전 가능', reason='')
        if receipt:
            from schedule_bundle import dependency_hashes
            key = job['key']
            copied = dict(job, source_repo=receipt['source_repo'], source_runtime=receipt['source_runtime'],
                          transfer_id=receipt['transfer_id'], not_before=max(
                              datetime.fromisoformat(receipt['released_at']),
                              datetime.fromisoformat(job.get('last_slot') or receipt['released_at'])).isoformat())
            imports[key] = copied
            now = max(datetime.now(KST), datetime.fromisoformat(copied['not_before']))
            shape = {'always': False, 'state': '등록됨', **cron_job(row['cron'])}
            next_at = job_next_start(shape, now)
            row['next_run'] = next_at.isoformat() if next_at else None
        if job['kind'] != KINDS[1] or job['key'] not in dispatch.TARGETS or not row['cron']:
            row.update(status='유지', reason='원본 실행 환경 또는 별도 작업 검증이 필요합니다.')
        elif profile['provider'] == 'Antigravity' and job['key'] not in dispatch.ANTIGRAVITY_TARGETS:
            row.update(status='대기', reason='Antigravity에 연결된 자료 수집·출력 경로가 아직 없는 작업입니다.')
        elif receipt and (row['previous_saved'] or job['key'] in imported
                          or (native.get(job['key'], {}).get('native') and native[job['key']].get('enabled') is not False)):
            row.update(status='대기', reason='대상 기기에 같은 작업이 등록돼 있거나 기존 인수 기록이 있습니다.')
        elif receipt and row['dependency_hashes'] != dependency_hashes(row['dependency_hashes']):
            row.update(status='대기', reason='대상 기기의 참조 문서나 스킬 버전이 원본과 다릅니다.')
        elif bundle is not None and bundle.get('origin') != origin(runtime):
            row.update(status='대기', reason='다른 기기의 원본 예약 정지는 현재 자동 확인할 수 없습니다.')
        elif not receipt and (not locally or not job.get('prompt_sha256')
              or row['cron'] != locally['schedule'].get('cron') or row['prompt_sha256'] != locally.get('prompt_sha256')
              or row['dependency_hashes'] != locally.get('dependency_hashes', {})):
            row.update(status='대기', reason='대상 기기에서 같은 원본 일정과 지시문을 확인할 수 없습니다.')
        elif not receipt and locally is not None and locally.get('state') in ('꺼짐', '일시 정지', '만료', '종료') and not locally.get('route'):
            row.update(status='유지', reason='기존 비활성 상태를 유지합니다.')
        elif not profile.get('fingerprint') or not profile.get('probe', {}).get('ok'):
            row.update(status='대기', reason='계정과 대상 모델의 실행 검증이 필요합니다.')
        elif (((locally or {}) if not receipt else job).get('provider') != profile['provider']
              or profile['model'] != (((locally or {}) if not receipt else job).get('last_model') or job.get('model'))):
            row.update(status='대체 모델 검증 필요', reason='검증 자료로 기존 작업의 필수 결과를 대조하세요.')
        else:
            row['reason'] = '대상 기기에서 인수합니다.' if receipt else '원본 예약의 일시 정지를 확인한 뒤 적용합니다.'
        rows.append(row)
    now = datetime.now(KST).isoformat()
    plan = {'id': f'schedule-{uuid.uuid4().hex[:12]}', 'target': target, 'profile': profile,
            'created': now, 'state': '계획', 'rows': rows, 'imports': imports}
    with closing(_db(runtime, write=True)) as db, db:
        _save(db, plan)
    return _public_plan(plan)


def _public_plan(plan):
    return {k: v for k, v in plan.items() if k not in ('profile', 'previous', 'imports')}


def verify(runtime, identifier):
    plan = _plan(runtime, identifier)
    result = _public_plan(plan)
    result['rows'] = [dict(row) for row in plan['rows']]
    for row in result['rows']:
        if row['status'] != '적용됨':
            continue
        history = dispatch.route_history(runtime, row['key'])
        row['first_run'] = next((r for r in reversed(history) if r['started'].isoformat() >= plan['applied']), None)
        if row['first_run']:
            first = row['first_run']
            row['first_run'] = {k: (v.isoformat() if isinstance(v, datetime) else v)
                                for k, v in first.items() if k != 'result'}
    return result


def _binding(db, task):
    return _lookup(db, 'schedule_bindings', task, 'task')


def raw_prompt(runtime, task):
    from schedule_imports import load
    if job := load(runtime).get(task):
        return job['prompt']
    return dispatch._skill(task).read_text(encoding='utf-8')


def bound_profile(runtime, route):
    from schedule_bundle import dependency_hashes

    with closing(_db(runtime)) as db:
        bound = _binding(db, route['task'])
    if not bound:
        return None
    prompt = raw_prompt(runtime, route['task'])
    if (bound['saved'] != route['saved'] or bound['prompt_sha256'] != hashlib.sha256(prompt.encode()).hexdigest()
            or bound.get('dependency_hashes', {}) != dependency_hashes(bound.get('dependency_hashes', {}))
            or bound['profile']['fingerprint'] != _account(bound['profile'])):
        raise ValueError('예약에 연결된 계정 또는 설정이 변경됐습니다.')
    return bound['profile']


def apply(runtime, identifier):
    from schedule_bundle import dependency_hashes, inventory
    from schedule_handoff import released
    from schedule_imports import catalog, definitions

    plan = _plan(runtime, identifier)
    if plan['state'] == '적용됨':
        return verify(runtime, identifier)
    if plan['state'] != '계획':
        raise ValueError('적용 가능한 예약 이전 계획이 아닙니다.')
    selected = [row for row in plan['rows'] if row['status'] == '이전 가능']
    if not selected:
        raise ValueError('현재 적용 가능한 작업이 없습니다. 검증을 마치고 새 계획을 만드세요.')
    profile = _profile(runtime, plan['target'])
    if profile != plan['profile'] or profile['fingerprint'] != _account(profile):
        raise ValueError('대상 프로필이나 로그인 계정이 변경됐습니다. 새 계획을 만드세요.')
    if not profile_runner_ready(runtime):
        raise ValueError('이전 기능을 지원하는 예약 AI 실행기가 동작해야 합니다.')
    jobs = {j['key']: j for j in catalog(runtime, claude_jobs())}
    actual = {j['id']: j for j in inventory(runtime)}
    peers = codex_jobs()
    for row in selected:
        if row['key'] in plan.get('imports', {}):
            local = jobs.get(row['key']) or {}
            if (local.get('native') and local.get('enabled') is not False
                    or row['dependency_hashes'] != dependency_hashes(row['dependency_hashes'])):
                raise ValueError(f"{row['name']}: 대상 기기의 중복 예약 또는 참조 자료를 확인하세요.")
            if reason := dispatch._block({'key': row['key'], 'native': True, 'enabled': False,
                                          'cron': row['cron'], 'times': [1]}, row, peers):
                raise ValueError(f"{row['name']}: {reason}")
            continue
        source = actual.get(row['id']) or {}
        if (source.get('schedule', {}).get('cron') != row['cron'] or source.get('prompt_sha256') != row['prompt_sha256']
                or source.get('dependency_hashes', {}) != row['dependency_hashes']):
            raise ValueError(f"{row['name']}: 원본 지시문이나 일정이 변경됐습니다.")
        if reason := dispatch._block(jobs.get(row['key']), row, peers):
            raise ValueError(f"{row['name']}: {reason}")
    now = datetime.now(KST).isoformat()
    with closing(_db(runtime, write=True)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        if _lookup(db, 'schedule_plans', identifier)['state'] != '계획':
            raise ValueError('다른 요청에서 계획 상태가 바뀌었습니다.')
        previous = {}
        for row in selected:
            task = row['key']
            if released(db, task):
                raise ValueError(f"{row['name']}: 다른 기기로 이미 넘긴 실행권입니다.")
            if definition := plan.get('imports', {}).get(task):
                if task in definitions(db):
                    raise ValueError(f"{row['name']}: 이 작업의 인수 영수증은 이미 사용했습니다.")
                db.execute('INSERT INTO schedule_imports VALUES (?, ?)', (task, json.dumps(definition, ensure_ascii=False)))
            if db.execute("SELECT 1 FROM runs WHERE task=? AND status='실행 중'", (task,)).fetchone():
                raise ValueError(f"{row['name']}: 실행 중인 회차가 있습니다.")
            old = db.execute('SELECT * FROM routes WHERE task=?', (task,)).fetchone()
            if (old['saved'] if old else None) != row['previous_saved']:
                raise ValueError(f"{row['name']}: 기존 실행 경로가 변경됐습니다. 새 계획을 만드세요.")
            previous[task] = {'route': dict(old) if old else None, 'binding': _binding(db, task)}
            db.execute('INSERT OR REPLACE INTO routes VALUES (?, ?, ?, ?, ?)',
                       (task, profile['provider'], profile['model'], row['cron'], now))
            binding = {'saved': now, 'profile': profile, 'prompt_sha256': row['prompt_sha256'],
                       'dependency_hashes': row['dependency_hashes']}
            db.execute('INSERT OR REPLACE INTO schedule_bindings VALUES (?, ?, ?)', (task, now, json.dumps(binding)))
            row['status'] = '적용됨'
        plan.update(state='적용됨', applied=now, previous=previous)
        _save(db, plan)
    return verify(runtime, identifier)


def profile_runner_ready(runtime):
    from os_services import Unknown, state

    try:
        running = state(dispatch.SERVICE)
        data = json.loads((runtime_root(runtime) / 'nhplug/schedule-runner-status.json').read_text(encoding='utf-8'))
        return (running['running'] and running.get('pid') and running['pid'] == data.get('pid')
                and data.get('profile_protocol') == dispatch.PROFILE_PROTOCOL)
    except (OSError, ValueError, Unknown, AttributeError):
        return False


def rollback(runtime, identifier):
    plan = _plan(runtime, identifier)
    if plan['state'] == '되돌림':
        return _public_plan(plan)
    if plan['state'] != '적용됨':
        raise ValueError('적용을 마친 계획만 되돌릴 수 있습니다.')
    with closing(_db(runtime, write=True)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        for task, previous in plan['previous'].items():
            current = db.execute('SELECT saved FROM routes WHERE task=?', (task,)).fetchone()
            running = db.execute("SELECT 1 FROM runs WHERE task=? AND status='실행 중'", (task,)).fetchone()
            if not current or current['saved'] != plan['applied'] or running:
                raise ValueError(f'{task}: 이후 변경 또는 실행 중 회차를 확인해 주세요.')
            db.execute('DELETE FROM schedule_bindings WHERE task=?', (task,))
            db.execute('DELETE FROM routes WHERE task=?', (task,))
            if old := previous['route']:
                db.execute('INSERT INTO routes VALUES (?, ?, ?, ?, ?)', tuple(old[k] for k in ('task', 'provider', 'model', 'cron', 'saved')))
            if bound := previous['binding']:
                db.execute('INSERT INTO schedule_bindings VALUES (?, ?, ?)', (task, bound['saved'], json.dumps(bound)))
        plan['state'] = '되돌림'
        for row in plan['rows']:
            if row['status'] == '적용됨':
                row['status'] = '되돌림'
        _save(db, plan)
    return _public_plan(plan)
