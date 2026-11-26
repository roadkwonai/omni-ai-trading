"""Portable, private snapshots of project automation definitions.

Reading a bundle never installs or executes its contents.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import tomllib
from datetime import datetime
from pathlib import Path

import schedules
import state_sync

VERSION = 1
SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$')
SECRET = re.compile(
    r'(?i)(?:sk-(?:ant-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|'
    r'https://(?:discord(?:app)?\.com)/api/webhooks/\S+|'
    r'(?:api[_-]?key|app[_-]?secret|access[_-]?token|password)\s*[:=]\s*["\']?[^\s"\']{12,})'
)
DEPENDENCY = re.compile(r'(?:\.agents|\.claude)/skills/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?')
REFERENCE = re.compile(r'(?<![\w/])references/[A-Za-z0-9_./-]+\.md')


def _read(path):
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None


def _codex_source(key, home):
    if not SAFE_ID.fullmatch(key):
        return {}
    base = home / '.codex'
    record = schedules._codex_records(base).get(key, {})
    try:
        entry = tomllib.loads((base / 'automations' / key / 'automation.toml').read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        entry = {}
    # Scheduler database supplies current metadata. File is a backup for older versions.
    return {**entry, **{k: v for k, v in record.items() if v is not None}}


def _claude_source(key, home):
    if not SAFE_ID.fullmatch(key):
        return None
    live = home / '.claude/scheduled-tasks' / key / 'SKILL.md'
    backup = schedules.REPO / 'ops/scheduled-tasks' / key / 'SKILL.md'
    return _read(live) or _read(backup)


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def origin(runtime=None):
    """Compare bundles from the same local scheduler without exposing the device name."""
    identity = f'{socket.gethostname()}:{state_sync.runtime_root(runtime).resolve()}'
    return hashlib.sha256(identity.encode()).hexdigest()


def dependency_hashes(names):
    hashes = {}
    for name in names:
        path = schedules.REPO / name
        if not path.suffix:
            path = path / 'SKILL.md'
        try:
            if '..' not in Path(name).parts and path.is_file():
                hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return hashes


def _dependencies(prompt):
    paths = set(DEPENDENCY.findall(prompt))
    roots = {'/'.join(name.split('/')[:3]) for name in paths}
    for root in roots:
        paths.update(f'{root}/{ref}' for ref in REFERENCE.findall(prompt))
    if re.search(r'\bAGENTS\.md\b', prompt):
        paths.add('AGENTS.md')
    return sorted(paths)


def inventory(runtime=None, home=None):
    """Return JSON-safe, source-labeled project jobs and their preserved prompt metadata."""
    home = Path(home or Path.home())
    from automation_status import processes
    from schedule_dispatch import route_rows
    from schedule_imports import load as imported_jobs

    jobs = schedules.all_jobs(processes(), home=home, runtime=runtime)
    imports = imported_jobs(runtime)
    claude, _ = state_sync.app_schedules(state_sync.app_stores(home), include_model=True)
    routes, _ = route_rows(runtime, jobs=[j for j in jobs if j['kind'] == schedules.KINDS[1]],
                           codex=[j for j in jobs if j['kind'] == schedules.KINDS[2]])
    out = []
    for job in jobs:
        kind, key = job['kind'], job['key']
        raw = {}
        prompt = None
        if kind == schedules.KINDS[1]:
            raw = (claude or {}).get(key, {})
            prompt = _claude_source(key, home)
            if job.get('imported'):
                imported = imports[key]
                prompt = imported['prompt']
                raw = {**imported['schedule'], 'reasoning_effort': imported.get('reasoning_effort')}
        elif kind == schedules.KINDS[2]:
            raw = _codex_source(key, home)
            prompt = raw.get('prompt')
        source_prompt = prompt if isinstance(prompt, str) else None
        unsafe = bool(source_prompt and SECRET.search(source_prompt))
        prompt = source_prompt if not unsafe else None
        digest = hashlib.sha256(prompt.encode('utf-8')).hexdigest() if prompt is not None else None
        days = job.get('days')
        route = routes.get(key) if kind == schedules.KINDS[1] else None
        dependencies = (imports[key].get('dependencies', []) if job.get('imported') else _dependencies(source_prompt or ''))
        schedule = {
            'timezone': 'Asia/Seoul', 'when': job.get('when') or '',
            'cron': raw.get('cron') or job.get('cron'),
            'rrule': raw.get('rrule'),
            'rrule_timezone': '미확인' if raw.get('rrule') else None,
            'once': raw.get('schedule'),
            'days': sorted(days) if days is not None else None,
            'monthday': job.get('monthday'),
            'date': list(job['date']) if job.get('date') else None,
            'times': [list(pair) for pair in job.get('times', [])],
            'always': bool(job.get('always')),
            'at_load': bool(job.get('at_load')),
            'next_run': _iso(job.get('next_run')),
        }
        output = {
            'id': f'{kind}:{key}', 'kind': kind, 'key': key,
            'name': job.get('name') or key, 'state': job.get('state') or '확인 불가',
            'provider': job.get('provider') or '', 'model': job.get('model') or '',
            'last_model': job.get('last_model'), 'last_run': _iso(job.get('last_run')),
            'schedule': schedule, 'when': schedule['when'],
            'prompt': prompt, 'prompt_sha256': digest,
            'prompt_status': '민감정보 확인 필요' if unsafe else '확인됨' if prompt is not None else '확인 불가',
            'dependencies': dependencies, 'dependency_hashes': dependency_hashes(dependencies),
            'skill': job.get('skill') or '', 'permission_mode': job.get('mode') or '',
            'output': job.get('what') or '', 'route': dict(route) if route else None,
            'source_kind': raw.get('kind'), 'source_thread_id': raw.get('target_thread_id'),
            'source_created_at': raw.get('created_at'),
            'reasoning_effort': raw.get('reasoning_effort'),
        }
        # Route DB fields are simple scalar values. Calendar objects are never serialized.
        out.append(output)
    return out


def validate_bundle(value):
    """Check schema, unique source IDs, and content digests before exposing a bundle."""
    if not isinstance(value, dict) or value.get('version') != VERSION or not isinstance(value.get('jobs'), list):
        raise ValueError('지원하지 않는 예약 번들 형식입니다.')
    seen = set()
    for job in value['jobs']:
        if not isinstance(job, dict):
            raise ValueError('작업 항목이 올바르지 않습니다.')
        kind, key = job.get('kind'), job.get('key')
        if kind not in schedules.KINDS or not isinstance(key, str) or not SAFE_ID.fullmatch(key):
            raise ValueError('작업 ID가 올바르지 않습니다.')
        ident = f'{kind}:{key}'
        if job.get('id') != ident or ident in seen:
            raise ValueError('작업 ID가 중복되었거나 일치하지 않습니다.')
        seen.add(ident)
        if not isinstance(job.get('name'), str) or not isinstance(job.get('schedule'), dict):
            raise ValueError('작업 이름 또는 일정이 올바르지 않습니다.')
        prompt, digest = job.get('prompt'), job.get('prompt_sha256')
        if prompt is not None:
            if not isinstance(prompt, str) or SECRET.search(prompt):
                raise ValueError('프롬프트가 올바르지 않거나 민감정보가 포함되어 있습니다.')
            if digest != hashlib.sha256(prompt.encode('utf-8')).hexdigest():
                raise ValueError('프롬프트 검증값이 일치하지 않습니다.')
        elif digest is not None:
            raise ValueError('프롬프트 없이 검증값이 저장되었습니다.')
    return value


def read_bundle(path):
    try:
        with Path(path).open(encoding='utf-8') as stream:
            return validate_bundle(json.load(stream))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('예약 번들을 읽을 수 없습니다.') from exc


def encode_bundle(runtime=None, home=None):
    bundle = {'version': VERSION, 'created_at': datetime.now(schedules.KST).isoformat(), 'origin': origin(runtime),
              'jobs': inventory(runtime=runtime, home=home)}
    validate_bundle(bundle)
    return (json.dumps(bundle, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def export_bundle(runtime, path):
    """Create a private bundle without replacing an existing file."""
    content = encode_bundle(runtime)
    file = Path(path).expanduser()
    fd = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        file.unlink(missing_ok=True)
        raise
    return {'path': str(file), 'jobs': len(validate_bundle(json.loads(content))['jobs'])}
