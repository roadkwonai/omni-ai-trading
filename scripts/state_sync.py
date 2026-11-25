"""Project portability: settings export for git, full state backup archive, import, verify, new-account check.

    PYTHONPATH=agent:scripts .venv/bin/python scripts/state_sync.py export            # state/settings (git) + state/backups/*.tar.gz
    PYTHONPATH=agent:scripts .venv/bin/python scripts/state_sync.py verify ARCHIVE
    PYTHONPATH=agent:scripts .venv/bin/python scripts/state_sync.py import ARCHIVE    # only while no trading service runs
    PYTHONPATH=agent:scripts .venv/bin/python scripts/state_sync.py check
    PYTHONPATH=agent:scripts .venv/bin/python scripts/state_sync.py tasks-export      # ~/.claude/scheduled-tasks -> ops/scheduled-tasks

Reads the runtime root (VIBE_TRADING_HOME or ~/.vibe-trading) and never writes there except `import`, which first moves every
file it would overwrite into <root>/restore-backup-<time>/ (nothing is deleted). Tokens, locks, .env and credentials are never
archived. SQLite files are copied with the sqlite3 backup API, never as raw files.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
REPO = Path(__file__).resolve().parents[1]
STATE = REPO / 'state'
TASKS_SRC = Path.home() / '.claude' / 'scheduled-tasks'
TASKS_DST = REPO / 'ops' / 'scheduled-tasks'
KEEP = 14
PREFIX = 'vibe-trading-state-'
# runtime-root children that belong to the trading state; everything else under the root (sessions, caches, tokens) is not
INCLUDE = ('nhplug', 'live')  # live: nh-activation.json and every HALT kill switch
SETTING_NAMES = ('accounts.json', 'strategy-settings.json', 'tunables.json', 'dashboard-prefs.json', 'ai-modes.json',
                 'watchlist-manual.json', 'settings.json', 'longterm-watchlist.json')
SECRET_PARTS = ('token', 'secret', 'credential', 'password', 'apikey', 'api_key')
SKIP_SUFFIX = ('.lock', '.plist', '.tmp', '-wal', '-shm', '-journal')
ACCOUNT_KEYS = ('hash', 'masked', 'account_number', 'account_no', 'acnt', 'cano')
HEX_KEY = re.compile(r'^[0-9a-f]{16,}$')
DIGITS = re.compile(r'\d{8,}|\d{3}-\d{2}-')


def now():
    return datetime.now(KST)


def runtime_root(override=None):
    if override:
        return Path(override).expanduser()
    env = os.environ.get('VIBE_TRADING_HOME', '').strip()
    return Path(env).expanduser() if env else Path.home() / '.vibe-trading'


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def is_secret(rel):
    parts = rel.lower().split('/')
    name = parts[-1]
    return (any(s in name for s in SECRET_PARTS) or name.startswith('.env') or name.endswith(SKIP_SUFFIX) or '.plist' in name
            or any(p == 'tokens' or p.startswith('restore-backup-') for p in parts[:-1]))


# --- sanitize ---------------------------------------------------------------------------------------------------------

def sanitize(value):
    """Copy for git: drop account numbers, hashes, masked numbers and anything shaped like one. Labels, budgets, rules and
    switches stay."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            low = str(key).lower()
            if low in ACCOUNT_KEYS or HEX_KEY.match(str(key)) or any(a in low for a in ('account_number', 'accountno')):
                continue
            out[key] = sanitize(item)
        return out
    if isinstance(value, list):
        return [sanitize(v) for v in value if not (isinstance(v, str) and (HEX_KEY.match(v) or DIGITS.search(v)))]
    if isinstance(value, str) and (HEX_KEY.match(value) or DIGITS.search(value)):
        return None
    return value


def settings_files(root):
    base = root / 'nhplug'
    if not base.is_dir():
        return []
    found = [p for p in base.rglob('*.json') if p.name in SETTING_NAMES and not is_secret(p.relative_to(root).as_posix())]
    return sorted(found)


def export_settings(root, dest):
    """Sanitized settings into dest (mirrors nhplug/ layout). Returns the written relative paths."""
    out = []
    for path in settings_files(root):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        rel = path.relative_to(root / 'nhplug')
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(sanitize(data), ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
        out.append(rel.as_posix())
    return out


# --- archive ----------------------------------------------------------------------------------------------------------

def candidates(root):
    for item in INCLUDE:
        top = root / item
        if top.is_file():
            yield top
        elif top.is_dir():
            for path in sorted(top.rglob('*')):
                if path.is_file() and not path.is_symlink() and not is_secret(path.relative_to(root).as_posix()):
                    yield path


def stage(root, staging):
    """Consistent copy of the trading state into staging; returns {relpath: (size, sha256)}."""
    files = {}
    for path in candidates(root):
        rel = path.relative_to(root).as_posix()
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in ('.sqlite3', '.db'):
            src = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30)
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        else:
            shutil.copy2(path, target)
        files[rel] = (target.stat().st_size, sha256(target))
    return files


def git_commit():
    try:
        return subprocess.run(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=10, encoding='utf-8').stdout.strip()
    except Exception:  # noqa: BLE001 - manifest info only
        return ''


def export_archive(root, out_dir, keep=KEEP):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now().strftime('%Y%m%d-%H%M')
    archive = out_dir / f'{PREFIX}{stamp}.tar.gz'
    with tempfile.TemporaryDirectory(dir=out_dir, prefix='.staging-') as tmp:
        staging = Path(tmp)
        files = stage(root, staging)
        manifest = {'created': now().isoformat(timespec='seconds'), 'runtime_root': str(root), 'git_commit': git_commit(),
                    'files': [{'path': r, 'size': s, 'sha256': h} for r, (s, h) in sorted(files.items())]}
        part = archive.with_suffix('.part')
        with tarfile.open(part, 'w:gz') as tar:
            body = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
            info = tarfile.TarInfo('manifest.json')
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
            for rel in files:
                tar.add(staging / rel, arcname=f'runtime/{rel}', recursive=False)
        os.replace(part, archive)
    for old in sorted(out_dir.glob(f'{PREFIX}*.tar.gz'))[:-keep]:
        old.unlink()
    return archive, manifest


def read_manifest(archive):
    with tarfile.open(archive, 'r:gz') as tar:
        return json.load(tar.extractfile('manifest.json'))


def safe_member(name):
    return name.startswith('runtime/') and '..' not in Path(name).parts and not name.startswith('/')


def verify(archive):
    """Returns a list of problems (empty = intact)."""
    problems = []
    try:
        manifest = read_manifest(archive)
    except Exception as exc:  # noqa: BLE001
        return [f'manifest unreadable: {exc}']
    expected = {f'runtime/{f["path"]}': f for f in manifest['files']}
    seen = set()
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            if member.name == 'manifest.json':
                continue
            if not safe_member(member.name) or not member.isfile():
                problems.append(f'unsafe member: {member.name}')
                continue
            entry = expected.get(member.name)
            if entry is None:
                problems.append(f'not in manifest: {member.name}')
                continue
            digest = hashlib.sha256()
            stream = tar.extractfile(member)
            for block in iter(lambda: stream.read(1 << 20), b''):
                digest.update(block)
            seen.add(member.name)
            if digest.hexdigest() != entry['sha256']:
                problems.append(f'checksum mismatch: {entry["path"]}')
    problems += [f'missing: {n[8:]}' for n in expected if n not in seen]
    return problems


# --- services / import ------------------------------------------------------------------------------------------------

def service_states():
    """os_services.states(), or None when the service manager cannot be asked (that is not "nothing runs")."""
    import os_services
    try:
        return os_services.states()
    except os_services.Unknown:
        return None


def running_services(states=...):
    """com.vibetrading.* services running now, except the dashboard; None when that is unknown."""
    states = service_states() if states is ... else states
    return None if states is None else [f'com.vibetrading.{n}' for n, row in states.items() if row['running'] and 'dashboard' not in n]


def import_archive(archive, root, force_offline=False, now_fn=now):
    problems = verify(archive)
    if problems:
        raise SystemExit('검증 실패: ' + '; '.join(problems[:5]))
    running = running_services()
    if running is None and not force_offline:
        raise SystemExit('서비스 상태를 확인할 수 없어 가져오지 않습니다.')
    if running and not force_offline:
        raise SystemExit('거래 서비스가 실행 중이라 가져올 수 없습니다: ' + ', '.join(running))
    manifest = read_manifest(archive)
    aside = root / f'restore-backup-{now_fn().strftime("%Y%m%d-%H%M%S")}'
    moved = restored = 0
    with tempfile.TemporaryDirectory(dir=root if root.is_dir() else None, prefix='.restore-') as tmp:
        with tarfile.open(archive, 'r:gz') as tar:
            for member in tar:
                if member.isfile() and safe_member(member.name):
                    target = Path(tmp) / member.name[8:]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with open(target, 'wb') as out:
                        shutil.copyfileobj(tar.extractfile(member), out)
        for entry in manifest['files']:
            rel = entry['path']
            target = root / rel
            if target.exists():
                for suffix in ('', '-wal', '-shm'):  # a stale WAL next to a restored db would corrupt it
                    old = Path(str(target) + suffix)
                    if old.exists():
                        dest = aside / (rel + suffix)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(old), dest)
                        moved += 1
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(Path(tmp) / rel), target)
            restored += 1
    return {'restored': restored, 'moved_aside': moved, 'aside': str(aside) if moved else '', 'created': manifest['created']}


# --- check ------------------------------------------------------------------------------------------------------------

def env_keys(path):
    keys = {}
    if path.is_file():
        for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
            m = re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', line)
            if m and not line.lstrip().startswith('#'):
                keys[m.group(1)] = bool(m.group(2).strip().strip('"\''))
    return keys


def archives(out_dir):
    return sorted(out_dir.glob(f'{PREFIX}*.tar.gz'), reverse=True)


def check(root, out_dir=None):
    out_dir = out_dir or STATE / 'backups'
    sys.path.insert(0, str(REPO / 'scripts'))
    have, template = env_keys(REPO / '.env'), env_keys(REPO / '.env.example')
    try:
        from launchd_services import SERVICES
    except Exception:  # noqa: BLE001
        SERVICES = {}
    import os_services
    states, agents = service_states(), os_services.definitions()
    defined = {p.stem.removeprefix('com.vibetrading.') for p in agents.glob('com.vibetrading.*')} if agents and agents.is_dir() else set()
    services = {n: ('plist' if n in defined else 'unknown') if states is None else 'loaded' if n in states else 'plist' if n in defined else 'missing' for n in SERVICES}
    manifest_file = TASKS_DST / 'manifest.json'
    wanted = [t['taskId'] for t in json.loads(manifest_file.read_text(encoding='utf-8'))] if manifest_file.exists() else []
    have_tasks = sorted(p.parent.name for p in TASKS_SRC.glob('*/SKILL.md')) if TASKS_SRC.is_dir() else []
    latest = archives(out_dir)[:1]
    return {
        'runtime_root': str(root), 'runtime_exists': (root / 'nhplug').is_dir(),
        'env_present': sorted(k for k, v in have.items() if v), 'env_empty': sorted(k for k, v in have.items() if not v),
        'env_missing_vs_example': sorted(set(template) - set(have)),
        'services': services, 'running_trading': running_services(states),
        'tasks_missing': sorted(set(wanted) - set(have_tasks)), 'tasks_installed': have_tasks, **task_problems(TASKS_DST),
        'latest_backup': latest[0].name if latest else '',
        'latest_backup_age_h': round((now().timestamp() - latest[0].stat().st_mtime) / 3600, 1) if latest else None,
    }


# --- scheduled tasks --------------------------------------------------------------------------------------------------

def task_problems(dst=TASKS_DST):
    """Manifest gaps that make ops-bootstrap ask instead of recreate: a task with neither cron nor a one-time schedule
    (tasks_export adds new tasks with cron null) and a task folder with no manifest entry."""
    manifest_file = dst / 'manifest.json'
    entries = json.loads(manifest_file.read_text(encoding='utf-8')) if manifest_file.exists() else []
    listed = {t['taskId'] for t in entries}
    return {'tasks_without_schedule': sorted(t['taskId'] for t in entries if not t.get('cron') and not t.get('schedule')),
            'tasks_not_in_manifest': sorted(p.name for p in dst.iterdir() if p.is_dir() and p.name not in listed) if dst.is_dir() else []}


def secret_hits(text):
    return [m.group(0)[:6] + '…' for m in re.finditer(r'sk-[A-Za-z0-9_-]{16,}|[A-Fa-f0-9]{32,}|\d{9,}', text)]


def app_stores(home=None):
    """The Claude app's own scheduled-task stores (where cron, one-time fireAt and enabled live; the SKILL.md files hold only
    the prompt). shortcut: an app-internal file whose path and format may change; tasks_export keeps the old manifest fields
    when none is found, and ops-bootstrap can still refill them from list_scheduled_tasks."""
    base = Path(home or Path.home()) / 'Library' / 'Application Support' / 'Claude' / 'claude-code-sessions'
    return sorted(base.glob('*/*/scheduled-tasks.json'), key=lambda f: f.stat().st_mtime) if base.is_dir() else []


def app_schedules(stores, repo=None, include_model=False):
    """({task id: {title, cron, schedule, enabled, permission_mode}} of this repo's tasks, {ids of other projects' tasks}) from
    the newest readable app store (stores sorted oldest first; an older account's store on this machine must not revive a
    task the current account deleted). The dict is None when no store could be read or it lists none of this repo's tasks
    (a new account before ops-bootstrap recreated them): then nothing is known to be deleted."""
    repo = str(repo or REPO)
    for store in reversed(list(stores)):
        try:
            tasks = json.loads(Path(store).read_text(encoding='utf-8')).get('scheduledTasks')
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(tasks, list):
            continue  # an unknown format is not "every task deleted"
        mine, foreign = {}, set()
        for t in tasks:
            if not isinstance(t, dict) or not t.get('id'):
                continue
            if t.get('cwd') and str(t['cwd']).rstrip('/') != repo.rstrip('/'):
                foreign.add(t['id'])
                continue
            fire = t.get('fireAt')
            mine[t['id']] = {'title': t.get('displayName') or '', 'cron': t.get('cronExpression') or None,
                             'schedule': datetime.fromtimestamp(fire / 1000, KST).isoformat() if type(fire) in (int, float) else None,
                             'enabled': t.get('enabled', True) is not False, 'permission_mode': t.get('permissionMode')}
            if include_model:
                raw_model = t.get('model') or t.get('modelId')
                mine[t['id']]['model'] = raw_model if isinstance(raw_model, str) else None
        return (mine or None), foreign
    return None, set()


def last_runs(stores):
    """{task id: when it last started} from the newest readable store (not in app_schedules: the backup manifest keeps only
    what recreates a task)."""
    for store in reversed(list(stores)):
        try:
            tasks = json.loads(Path(store).read_text(encoding='utf-8'))['scheduledTasks']
            return {t['id']: datetime.fromisoformat(t['lastRunAt']).astimezone(KST) for t in tasks if t.get('lastRunAt')}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return {}


def tasks_export(src=TASKS_SRC, dst=TASKS_DST, stores=None):
    """Copy each scheduled task's SKILL.md and write the manifest ops-bootstrap recreates them from. The schedule (cron,
    one-time time, enabled, title, permission mode) comes from the Claude app's store (user 2026-11-19: another account
    must get every task back on the same schedule); a task the app no longer has is dropped from the backup, and another
    project's task is never copied in. ops/ is not in git, so the backup is only ever pruned against a store that lists
    this repo's tasks: without one (new machine or account before restore, unreadable store) every backed-up task stays."""
    manifest_file = dst / 'manifest.json'
    old = {t['taskId']: t for t in json.loads(manifest_file.read_text(encoding='utf-8'))} if manifest_file.exists() else {}
    app, foreign = app_schedules(app_stores() if stores is None else stores)
    entries, warnings = [], []
    for skill in sorted(src.glob('*/SKILL.md')):
        task = skill.parent.name
        if task in foreign or (app is not None and task not in app):
            continue  # another project's task, or deleted in the app (its SKILL.md stays on disk for recovery)
        text = skill.read_text(encoding='utf-8')
        if secret_hits(text):
            warnings.append(f'{task}: 비밀값처럼 보이는 문자열 {secret_hits(text)}')
        head = re.search(r'^description:\s*(.*)$', text, re.M)
        (dst / task).mkdir(parents=True, exist_ok=True)
        (dst / task / 'SKILL.md').write_text(text, encoding='utf-8')
        prev = old.get(task, {})
        sched = (app or {}).get(task) or {'title': prev.get('title', ''), 'cron': prev.get('cron'), 'schedule': prev.get('schedule'),
                                          'enabled': prev.get('enabled', True), 'permission_mode': prev.get('permission_mode')}
        entries.append({'taskId': task, 'description': head.group(1).strip() if head else '', **sched})
    done = {e['taskId'] for e in entries}
    for task, prev in old.items():  # backed up but not installed here (yet): keep unless the app says it is gone
        if task not in done and task not in foreign and (app is None or task in app) and (dst / task / 'SKILL.md').exists():
            entries.append({**prev, **(app or {}).get(task, {})})
    if app is not None:
        keep = {e['taskId'] for e in entries}
        for folder in (dst.iterdir() if dst.is_dir() else []):
            if folder.is_dir() and folder.name not in keep:
                shutil.rmtree(folder)  # the backup copy of a task the app deleted
    entries.sort(key=lambda e: e['taskId'])
    dst.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return entries, warnings


# --- cli --------------------------------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('command', choices=['export', 'import', 'verify', 'check', 'tasks-export'])
    parser.add_argument('archive', nargs='?')
    parser.add_argument('--runtime', help='runtime root (default VIBE_TRADING_HOME or ~/.vibe-trading)')
    parser.add_argument('--out', help='state folder (default <repo>/state)')
    parser.add_argument('--force-offline', action='store_true',
                        help='import even though services run or their state is unknown: only when you know they are not this checkout\'s '
                             'trading services (for example a fresh machine); the dashboard never offers this')
    args = parser.parse_args(argv)
    root = runtime_root(args.runtime)
    state = Path(args.out) if args.out else STATE
    if args.command == 'export':
        written = export_settings(root, state / 'settings')
        try:  # the scheduled tasks and their schedules move with the state too (user 2026-11-19)
            tasks, _ = tasks_export()
            print(f'scheduled tasks {len(tasks)} -> {TASKS_DST}')
        except (OSError, ValueError, KeyError, TypeError) as exc:  # the archive below must still be written today
            print(f'scheduled tasks NOT exported: {type(exc).__name__}: {exc}')
        archive, manifest = export_archive(root, state / 'backups')
        print(f'settings {len(written)} files -> {state / "settings"}')
        print(f'archive {archive} {archive.stat().st_size / 1e6:.1f}MB, {len(manifest["files"])} files')
    elif args.command == 'verify':
        problems = verify(Path(args.archive))
        print('OK' if not problems else '\n'.join(problems))
        return 1 if problems else 0
    elif args.command == 'import':
        print(json.dumps(import_archive(Path(args.archive), root, args.force_offline), ensure_ascii=False, indent=1))
    elif args.command == 'check':
        print(json.dumps(check(root, state / 'backups'), ensure_ascii=False, indent=1))
    else:
        entries, warnings = tasks_export()
        print(f'{len(entries)} tasks -> {TASKS_DST}')
        print('\n'.join(warnings))
        print('cron 없음: ' + ', '.join(e['taskId'] for e in entries if not e['cron']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
