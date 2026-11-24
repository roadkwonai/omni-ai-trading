"""One answer shape for every command, exit codes, and what never leaves the CLI (account numbers, keys, webhooks)."""
from __future__ import annotations

import functools
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import click

KST = ZoneInfo('Asia/Seoul')
KINDS = {1: 'failed', 2: 'usage', 3: 'approval_required', 4: 'refused', 5: 'stale', 6: 'unavailable'}
HIDDEN_KEYS = {'hash', 'masked', 'act_no', 'acct_no', 'account_number', 'scope'}
PATTERNS = [re.compile(p) for p in (r'(?<!\d)\d{3}-\d{2}-\d{6}(?!\d)', r'https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\S+',
                                    r'https://hooks\.slack\.com/\S+', r'\b(?:sk|ghp|gho|github_pat)[-_][A-Za-z0-9_-]{16,}')]


class OmniError(Exception):
    def __init__(self, message, code=1, data=None, next=()):
        super().__init__(message)
        self.code, self.data, self.next = code, data or {}, list(next)


class Refused(OmniError):
    def __init__(self, message, code=4, **kw):
        super().__init__(message, code, **kw)


@functools.lru_cache(maxsize=1)
def secrets():
    """Secret values of .env, only to mask them in an output that somehow holds one."""
    try:
        from dotenv import dotenv_values
        from local_env import ENV
        found = dotenv_values(ENV, interpolate=False)
    except Exception:  # noqa: BLE001 - masking by pattern still works
        return ()
    public = ('REPO', 'URL', 'BRANCH', 'PUSH', 'NAME', 'EMAIL')
    values = {v for k, v in found.items() if v and len(v) >= 8 and (('WEBHOOK' in k) or not any(p in k for p in public))}
    numbers = {re.sub(r'\D', '', v) for v in values if len(re.sub(r'\D', '', v)) == 11}
    # an account number is short enough to be found from its plain SHA-256, so the stored digests stay inside too
    digests = {hashlib.sha256((prefix + n).encode()).hexdigest() for n in numbers for prefix in ('', 'nh-live:', 'nh-paper:')}
    try:
        import accounts
        digests |= {r['hash'] for r in accounts.load().values() if isinstance(r.get('hash'), str) and len(r['hash']) == 64}
    except Exception:  # noqa: BLE001
        pass
    return tuple(sorted(values | numbers | digests | {d[:16] for d in digests}, key=len, reverse=True))


def clean(value):
    """`value` without hidden keys and with secrets masked."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items() if str(k) not in HIDDEN_KEYS}
    if isinstance(value, (list, tuple, set)):
        return [clean(v) for v in value]
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        for secret in secrets():
            value = value.replace(secret, '***')
        for pattern in PATTERNS:
            value = pattern.sub('***', value)
        return value
    return value if value is None or isinstance(value, (bool, int, float)) else str(value)


def source(path):
    """Where an answer came from and how old it is."""
    path, newest = Path(path), None
    for file in (path, path.with_name(path.name + '-wal')):  # a WAL database changes there until a checkpoint
        try:
            newest = max(newest or 0, file.stat().st_mtime)
        except OSError:
            continue
    return {'path': path.name, 'missing': True} if newest is None else {'path': path.name, 'age_s': max(0, round(datetime.now().timestamp() - newest))}


def age_text(seconds):
    for size, unit in ((86400, '일'), (3600, '시간'), (60, '분')):
        if seconds >= size:
            return f'{int(seconds // size)}{unit} 전'
    return '방금'


TEXT_LIMIT = 20_000


def shorten(shown):
    return shown if len(shown) <= TEXT_LIMIT else shown[:TEXT_LIMIT] + f'\n… 길어서 {len(shown) - TEXT_LIMIT:,}자를 줄였습니다. 전체는 --json으로 보세요.'


def plain(value):
    """One value as a person reads it; numbers keep what matters (JSON keeps them whole)."""
    if value in (None, [], {}):
        return '-'
    if isinstance(value, float):
        return f'{value:,.0f}' if abs(value) >= 1000 else f'{value:.4g}'
    return value


def text(value, indent=0):
    pad = '  ' * indent
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                lines += [f'{pad}{k}:', text(v, indent + 1)]
            else:
                lines.append(f'{pad}{k}: {plain(v)}')
        return '\n'.join(lines)
    if isinstance(value, list):
        if value and all(isinstance(r, dict) for r in value) and all(not isinstance(v, (dict, list)) for r in value for v in r.values()):
            return '\n'.join(f'{pad}- ' + ' · '.join(f'{k} {plain(v)}' for k, v in r.items() if v not in (None, '')) for r in value)
        return '\n'.join(f'{pad}- {v}' if not isinstance(v, (dict, list)) else f'{pad}-\n{text(v, indent + 1)}' for v in value)
    return f'{pad}{value}'


def emit(ctx, command, ok, data=None, error=None, code=0, sources=(), warnings=()):
    body = clean({'ok': ok, 'command': command, 'code': code, 'time': datetime.now(KST).isoformat(timespec='seconds'),
                  **({'error': error} if error else {}), 'data': data if data is not None else {}, 'source': list(sources),
                  'warnings': list(warnings)})
    if ctx.find_root().obj['json']:
        click.echo(json.dumps(body, ensure_ascii=False, default=str))
        return
    if error:
        click.echo(body['error']['message'], err=True)
        for step in body['error']['next']:
            click.echo(f'  다음: {step}', err=True)
    if body['data']:
        click.echo(shorten(text(body['data'])))
    for line in body['warnings']:
        click.echo(f'주의: {line}', err=True)
    for s in body['source']:
        click.echo(f"자료: {s['path']} " + ('없음' if s.get('missing') else age_text(s.get('age_s'))), err=True)


class Answer:
    def __init__(self, data, sources=(), warnings=(), code=0):
        self.data, self.sources, self.warnings, self.code = data, sources, warnings, code


def command(fn):
    """A Click command body that returns data (or an Answer) and may raise OmniError."""
    @functools.wraps(fn)
    @click.pass_context
    def run(ctx, *args, **kwargs):
        name = ctx.command_path.split(' ', 1)[-1]
        try:
            from ..utils import omni_backend  # every command body may import the checkout's modules
            omni_backend.load()
            got = fn(*args, **kwargs)
        except OmniError as exc:
            emit(ctx, name, False, exc.data, {'type': KINDS.get(exc.code, 'failed'), 'message': str(exc), 'next': exc.next}, exc.code)
            ctx.exit(exc.code)
        except (click.ClickException, click.exceptions.Exit, click.Abort):
            raise
        except Exception as exc:  # noqa: BLE001 - the type only: a message may hold a path or an account
            emit(ctx, name, False, None, {'type': 'failed', 'message': f'실패했습니다({type(exc).__name__}).', 'next': []}, 1)
            ctx.exit(1)
        got = got if isinstance(got, Answer) else Answer(got)
        error = {'type': KINDS.get(got.code, 'failed'), 'message': '점검에서 문제가 나왔습니다.', 'next': []} if got.code else None
        emit(ctx, name, got.code == 0, got.data, error, got.code, got.sources, got.warnings)
        if got.code:
            ctx.exit(got.code)
    return run
