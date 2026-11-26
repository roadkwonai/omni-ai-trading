"""Destination-owned definitions for jobs released by another Omni installation."""
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from schedules import KINDS, REPO, cron_job
from state_sync import runtime_root


def definitions(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='schedule_imports'").fetchone():
        return {}
    return {task: json.loads(data) for task, data in db.execute('SELECT task, data FROM schedule_imports')}


def load(runtime):
    from schedule_dispatch import _open

    try:
        with closing(_open(runtime)) as db:
            return definitions(db)
    except FileNotFoundError:
        return {}


def translated_prompt(definition, runtime):
    """Rebind only the source repository and runtime paths in an imported task."""
    prompt = definition['prompt']
    replacements = (
        (definition['source_repo'], str(REPO)),
        (definition['source_runtime'], str(runtime_root(runtime))),
        ('~/.vibe-trading', str(runtime_root(runtime))),
    )
    # Replace the longer paths first so nested runtime folders can have independent destinations.
    import re
    table = {old: new for old, new in replacements if Path(old).is_absolute() or old == '~/.vibe-trading'}
    if not table:
        return prompt
    pattern = '|'.join(re.escape(path) for path in sorted(table, key=len, reverse=True))
    return re.sub(f'(?:{pattern})(?![A-Za-z0-9_.-])', lambda m: table[m.group()], prompt)


def catalog(runtime, native):
    by_key = {job['key']: job for job in native}
    imports = load(runtime)
    if not imports:
        return native
    from state_sync import app_schedules, app_stores

    stores = app_stores()
    readable = not stores or app_schedules(stores, include_model=True)[0] is not None
    # An app with only other projects can legitimately contain no project jobs.
    if not readable:
        try:
            data = json.loads(stores[-1].read_text(encoding='utf-8'))
            readable = isinstance(data.get('scheduledTasks'), list)
        except (OSError, ValueError, AttributeError):
            pass
    for key, job in imports.items():
        local = by_key.get(key) or {}
        duplicate = not readable or bool(local.get('native') and local.get('enabled') is not False)
        schedule = job['schedule']
        by_key[key] = {'kind': KINDS[1], 'key': key, 'name': job['name'], 'when': schedule['when'],
                       'what': job.get('output') or '', 'mode': job.get('permission_mode') or '',
                       'skill': job.get('skill') or '', 'provider': job['provider'], 'model': job['model'],
                       'state': '이전 대기', 'native': True, 'enabled': duplicate, 'imported': True,
                       'always': False, 'cron': schedule['cron'], **cron_job(schedule['cron'])}
    return list(by_key.values())
