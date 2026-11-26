"""Antigravity CLI adapter for isolated, explicitly keyed Gemini sessions."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}\Z')


def _credential(path):
    """Read a local owner-only regular file without following its final symlink."""
    if not path:
        raise ValueError('Antigravity Gemini API 키 파일을 지정하세요.')
    target = Path(path).expanduser()
    if not target.is_absolute():
        raise ValueError('API 키 파일은 절대 경로로 지정하세요.')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(target, flags)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 8192):
                raise ValueError('API 키 파일 소유자와 접근 권한을 확인하세요.')
            key = os.read(fd, 8193).decode('utf-8').strip()
        finally:
            os.close(fd)
    except (OSError, UnicodeError) as exc:
        raise ValueError('API 키 파일을 안전하게 읽을 수 없습니다.') from exc
    if not key or len(key) > 4096 or any(ch.isspace() for ch in key):
        raise ValueError('API 키 파일 내용이 올바르지 않습니다.')
    return key


def account(profile):
    """Stable fingerprint tied to the key, without persisting the key."""
    key = _credential(profile.get('credential_file'))
    return hashlib.sha256(('omni-antigravity-key-v1:' + key).encode()).hexdigest()


def env(profile):
    values = {name: os.environ[name] for name in ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR')
              if name in os.environ}
    values['GEMINI_API_KEY'] = _credential(profile.get('credential_file'))
    return values


def command(binary, model, work):
    """Restrict all filesystem, shell, network and MCP tool access for fixture-only runs."""
    if not MODEL.fullmatch(model or ''):
        raise ValueError('모델 ID가 올바르지 않습니다.')
    settings = {'modelProvider': 'gemini', 'toolPermission': 'request-review',
                'permissions': {'deny': ['command(*)', 'write_file(*)', 'read_file(*)',
                                         'read_url(*)', 'execute_url(*)', 'mcp(*)']}}
    return [str(binary), '--input-format', 'stream-json', '--output-format', 'stream-json',
            '--model', model, '--settings', json.dumps(settings), '--disable-slash-commands',
            '--print-timeout', '60s']


def input_text(prompt):
    return json.dumps({'event': 'user', 'message': {'content': prompt}}, ensure_ascii=False) + '\n'


def response(stdout, stderr=''):
    """Require a terminal SUCCESS and reject tool actions and denied operations."""
    if re.search(r'(permission|approval|denied|거부)', stderr, re.I):
        return None, None
    result, model = None, None
    try:
        for line in stdout.splitlines():
            event = json.loads(line)
            if event.get('event') == 'init':
                model = (event.get('init') or {}).get('model')
            if event.get('event') == 'step_update' and (event.get('step_update') or {}).get('step_type') == 'tool':
                return None, None
            if event.get('event') == 'result':
                result = event.get('result')
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None, None
    if not isinstance(result, dict) or result.get('status') != 'SUCCESS':
        return None, None
    text = result.get('response')
    return (text, model) if isinstance(text, str) and text.strip() else (None, None)


def test_model(profile, binary, prompt, work, timeout=60):
    try:
        out = subprocess.run(command(binary, profile['model'], work), input=input_text(prompt), capture_output=True,
                             text=True, cwd=str(work), env=env(profile), timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Antigravity 모델 검증에 실패했습니다.') from exc
    return response(out.stdout, out.stderr) if out.returncode == 0 else (None, None)
