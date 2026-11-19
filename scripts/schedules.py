"""Every scheduled job in one shape for the dashboard's 운영 → 자동 작업 (user 2026-11-19): launchd services
(scripts/launchd_services.py), Claude scheduled tasks (the Claude app's own store, else the ops/scheduled-tasks backup)
and this repo's Codex automations (~/.codex/automations). Read-only.

A job: {kind, key, name, when, what, state, days (weekdays 0=Mon, or None), monthday, date, times [(h, m)], always,
mode}. runs_on(job, day) and day_plan(jobs, day) give one day's timeline.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KINDS = ('launchd 서비스', 'Claude 예약 작업', 'Codex 자동화')
WEEKDAYS = frozenset(range(5))
RRULE_DAYS = {'MO': 0, 'TU': 1, 'WE': 2, 'TH': 3, 'FR': 4, 'SA': 5, 'SU': 6}


def field(text, low, high):
    """The values of one cron field: '*', '*/n', 'a', 'a-b', 'a,b' and their mix."""
    out = set()
    for part in str(text).split(','):
        step = 1
        if '/' in part:
            part, step = part.split('/', 1)
            step = int(step)
        if part == '*':
            a, b = low, high
        elif '-' in part:
            a, b = (int(x) for x in part.split('-', 1))
        else:
            a = b = int(part)
        out.update(range(a, b + 1, step))
    return sorted(out)


def cron_job(cron):
    """{days, monthday, date, times} of a five-field cron in local time (Sunday is 0 or 7 in cron, 6 here)."""
    minute, hour, dom, month, dow = cron.split()
    times = [(h, m) for h in field(hour, 0, 23) for m in field(minute, 0, 59)]
    days = None if dow == '*' else frozenset((d - 1) % 7 for d in field(dow, 0, 7))
    if dom != '*' and month != '*':
        return {'days': days, 'monthday': None, 'date': (int(month), int(dom)), 'times': times}
    return {'days': days if days is not None or dom != '*' else frozenset(range(7)),
            'monthday': int(dom) if dom != '*' else None, 'date': None, 'times': times}


def runs_on(job, day):
    """True when `job` has a start on `day` (a date); an always-on service never 'starts' in the timeline."""
    if job['always']:
        return False
    if job['date'] is not None:
        return (day.month, day.day) == job['date'][-2:] and (len(job['date']) == 2 or day.year == job['date'][0])
    if job['monthday'] is not None:
        return day.day == job['monthday'] and (job['days'] is None or day.weekday() in job['days'])
    return job['days'] is not None and day.weekday() in job['days']


def day_plan(jobs, day):
    """[(h, m, job)] of every start on `day`, by time."""
    return sorted(((h, m, j) for j in jobs if runs_on(j, day) for h, m in j['times']), key=lambda t: (t[0], t[1], t[2]['name']))


def clock(h, m):
    return f'{h:02}:{m:02}'


def times_text(times):
    """'08:00~08:55 5분마다 · 09:00' style: three or more evenly spaced starts read as one span."""
    gap = lambda a, b: (b[0] * 60 + b[1]) - (a[0] * 60 + a[1])  # noqa: E731
    out, i = [], 0
    while i < len(times):
        j = i + 1
        if j < len(times):
            step = gap(times[i], times[j])
            while j + 1 < len(times) and gap(times[j], times[j + 1]) == step:
                j += 1
            if j - i >= 2 and step > 0:
                out.append(f"{clock(*times[i])}~{clock(*times[j])} {'매시' if step == 60 else f'{step}분마다'}")
                i = j + 1
                continue
        out.append(clock(*times[i]))
        i += 1
    return ' · '.join(out)


def days_text(days):
    return '평일' if days == WEEKDAYS else '매일' if days == frozenset(range(7)) else ','.join('월화수목금토일'[d] for d in sorted(days))


def launchd_jobs(states=None):
    import launchd_services as LS
    from automation_status import NAMES
    states = states or {}
    out = []
    for name, (args, _, at_load, schedule) in LS.SERVICES.items():
        label = NAMES.get(name, name)
        times = [(h, m, ds) for h, m, ds in LS._times(schedule)] if schedule else []
        days = frozenset(d - 1 for _, _, ds in times for d in ds)
        out.append({'kind': KINDS[0], 'key': f'com.vibetrading.{name}', 'name': label,
                    'when': f"{days_text(days)} {times_text(sorted({(h, m) for h, m, _ in times}))}" if schedule else '상시 (로그인 시 시작)',
                    'what': ' '.join(args), 'state': states.get(label, '확인 불가'), 'always': not schedule,
                    'days': days if times else None, 'monthday': None, 'date': None,
                    'times': sorted({(h, m) for h, m, _ in times}), 'mode': '', 'at_load': at_load})
    return out


def claude_entries():
    """Claude tasks: the app's own store (what is scheduled now), else the backup manifest; KNOWN adds skill and result."""
    import skill_catalog as cat
    backup = {j['key']: j for j in cat.jobs()}
    try:
        from state_sync import app_schedules, app_stores
        app, _ = app_schedules(app_stores())
    except Exception:  # noqa: BLE001 - an unreadable app store: the backup stands
        app = None
    if not app:
        return list(backup.values())
    out = []
    for key, s in app.items():
        old = backup.get(key, {})
        known = cat.KNOWN.get(key, {})
        when = cat.cron_text(s['cron']) if s['cron'] else (f"{s['schedule'][5:7].lstrip('0')}/{s['schedule'][8:10]} {s['schedule'][11:16]} 한 번"
                                                           if s.get('schedule') else '')
        out.append({**old, 'key': key, 'name': s['title'] or old.get('name') or key, 'runner': 'Claude 예약 작업', 'when': when,
                    'skill': known.get('skill', old.get('skill', '')), 'result': old.get('result') or known.get('result') or description(key),
                    'enabled': s['enabled'], 'cron': s['cron'], 'schedule': s['schedule'], 'permission_mode': s['permission_mode']})
    return out


def description(key, home=None):
    """The task's own one-line description (its SKILL.md front matter), '' when not there."""
    try:
        text = (Path(home or Path.home()) / '.claude/scheduled-tasks' / key / 'SKILL.md').read_text(encoding='utf-8')
    except OSError:
        return ''
    found = re.search(r'^description:\s*(.*)$', text, re.M)
    return found.group(1).strip() if found else ''


def claude_jobs(home=None):
    import skill_catalog as cat
    out = []
    for e in claude_entries():
        shape = {'days': None, 'monthday': None, 'date': None, 'times': []}
        if e.get('cron'):
            try:
                shape = cron_job(e['cron'])
            except ValueError:
                pass
        elif e.get('schedule'):
            at = datetime.fromisoformat(e['schedule'])
            shape = {'days': None, 'monthday': None, 'date': (at.year, at.month, at.day), 'times': [(at.hour, at.minute)]}
        out.append({'kind': KINDS[1], 'key': e['key'], 'name': e['name'], 'when': e['when'], 'what': e.get('result') or '',
                    'state': cat.job_state(e, home), 'always': False, 'mode': e.get('permission_mode') or '',
                    'skill': e.get('skill', ''), **shape})
    return out


def codex_jobs(home=None, repo=REPO):
    """This repo's Codex automations (prompt or cwds name the repo), with their RRULE days and times."""
    out = []
    for toml in sorted((Path(home or Path.home()) / '.codex/automations').glob('*/automation.toml')):
        text = toml.read_text(encoding='utf-8', errors='replace')
        if str(repo) not in text:
            continue
        get = lambda k: (re.search(rf'^{k}\s*=\s*"([^"]*)"', text, re.M) or [None, ''])[1]  # noqa: E731
        rule = dict(p.split('=', 1) for p in get('rrule').split(';') if '=' in p)
        days = frozenset(RRULE_DAYS[d] for d in rule.get('BYDAY', '').split(',') if d in RRULE_DAYS) or frozenset(range(7))
        hours = [int(h) for h in rule.get('BYHOUR', '').split(',') if h.isdigit()]
        minutes = [int(m) for m in rule.get('BYMINUTE', '0').split(',') if m.isdigit()] or [0]
        times = [(h, m) for h in hours for m in minutes]
        status = get('status')
        out.append({'kind': KINDS[2], 'key': toml.parent.name, 'name': get('name') or toml.parent.name,
                    'when': f'{days_text(days)} {times_text(times)}',
                    'what': '', 'state': {'ACTIVE': '실행 중', 'PAUSED': '일시 정지'}.get(status, status or '확인 불가'),
                    'always': False, 'mode': '', 'days': days if status == 'ACTIVE' else frozenset(), 'monthday': None, 'date': None,
                    'times': times})
    return out


def all_jobs(states=None, home=None):
    return launchd_jobs(states) + claude_jobs(home) + codex_jobs(home)


def next_start(jobs, now):
    """(datetime, job) of the next start from `now` (today or the next eight days), or None."""
    from datetime import timedelta
    for offset in range(8):
        day = (now + timedelta(days=offset)).date()
        for h, m, job in day_plan(jobs, day):
            at = datetime.combine(day, datetime.min.time(), now.tzinfo).replace(hour=h, minute=m)
            if at > now and job['state'] not in ('꺼짐', '일시 정지', '없음'):
                return at, job
    return None

