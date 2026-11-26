"""Inventory and KST calendar for launchd, Claude and Codex project schedules."""
from __future__ import annotations

import json
import os
import plistlib
import re
import sqlite3
import tomllib
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
KINDS = ('launchd 서비스', 'Claude 예약 작업', 'Codex 자동화')
WEEKDAYS = frozenset(range(5))
RRULE_DAYS = {'MO': 0, 'TU': 1, 'WE': 2, 'TH': 3, 'FR': 4, 'SA': 5, 'SU': 6}
KST = ZoneInfo('Asia/Seoul')
NON_ACTIVE = {'꺼짐', '일시 정지', '없음', '미등록', '확인 불가', '전환 대기', '이전 대기', '만료', '기록만 있음', '백업'}


def timestamp(value):
    try:
        if type(value) in (int, float):
            return datetime.fromtimestamp(value / 1000, KST)
        when = datetime.fromisoformat(value)
        return when.astimezone(KST) if when.tzinfo else when.replace(tzinfo=KST)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


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
        if not low <= a <= b <= high or step < 1:
            raise ValueError(text)
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
    if job['always'] or job['state'] in NON_ACTIVE:
        return False
    if job.get('calendar'):
        return bool(starts_on(job, day))
    if job['date'] is not None:
        return (day.month, day.day) == job['date'][-2:] and (len(job['date']) == 2 or day.year == job['date'][0])
    if job['monthday'] is not None:
        return day.day == job['monthday'] and (job['days'] is None or day.weekday() in job['days'])
    return job['days'] is not None and day.weekday() in job['days']


def day_plan(jobs, day):
    """[(h, m, job)] of every start on `day`, by time."""
    return sorted(((h, m, j) for j in jobs for h, m in starts_on(j, day)), key=lambda t: (t[0], t[1], t[2]['name']))


def starts_on(job, day):
    if job['always'] or job['state'] in NON_ACTIVE:
        return []
    if (calendar := job.get('calendar')) is not None:
        start = datetime.combine(day, time.min, KST)
        return sorted({(kst.hour, kst.minute)
                       for at in calendar.between(start, start + timedelta(days=1), inc=True)
                       if (kst := at.astimezone(KST)).date() == day})
    if job.get('slot_days'):
        return [hm for hm, days in job['slot_days'].items() if day.weekday() in days]
    return job['times'] if runs_on(job, day) else []


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


def cron_text(shape):
    day = shape['date']
    period = (f'{day[-2]}/{day[-1]}' if day else f"매월 {shape['monthday']}일" if shape['monthday']
              else days_text(shape['days']) if shape['days'] else '')
    return ' '.join(filter(None, (period, times_text(shape['times']))))


def launchd_jobs(states=None, home=None, runtime=None):
    import launchd_services as LS
    from automation_status import NAMES
    from schedule_models import configured

    root = _nhplug(runtime, home)
    home = Path(home or Path.home())
    states = states or {}
    out = []
    definitions = {f'com.vibetrading.{name}': LS.render(name, home=home) for name in LS.SERVICES}
    installed = {}
    for path in (Path(home or Path.home()) / 'Library/LaunchAgents').glob('com.vibetrading.*.plist'):
        try:
            data = plistlib.loads(path.read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        if data.get('WorkingDirectory') == str(REPO) and data.get('Label') == path.stem:
            installed[path.stem] = data
    for key, data in (definitions | installed).items():
        name = key.removeprefix('com.vibetrading.')
        label = NAMES.get(name, name)
        intervals = data.get('StartCalendarInterval', [])
        intervals = [intervals] if isinstance(intervals, dict) else intervals
        times = {}
        for interval in intervals:
            if {'Hour', 'Minute'} <= interval.keys() and not {'Month', 'Day'} & interval.keys():
                weekdays = {(interval['Weekday'] - 1) % 7} if 'Weekday' in interval else set(range(7))
                times.setdefault((interval['Hour'], interval['Minute']), set()).update(weekdays)
        grouped = {}
        for hm, ds in sorted(times.items()):
            grouped.setdefault(frozenset(ds), []).append(hm)
        days = frozenset(d for ds in grouped for d in ds)
        planned = ' · '.join(f'{days_text(ds)} {times_text(ts)}' for ds, ts in grouped.items())
        if intervals and not planned:
            planned = '사용자 지정 캘린더'
        if data.get('StartInterval') and name != 'ai-schedule-runner':
            planned = f"{data['StartInterval']}초마다"
        at_load = bool(data.get('RunAtLoad'))
        when = ' · '.join(filter(None, (planned, '로그인 시 시작' if at_load else ''))) or '수동 시작'
        proc = states.get(label, states.get(name, '확인 불가'))
        models = configured(name, root, home, data.get('EnvironmentVariables') or {}, known=name in LS.SERVICES)
        out.append({'kind': KINDS[0], 'key': key, 'name': label, 'when': when,
                    'what': ' '.join(LS.SERVICES[name][0]) if name in LS.SERVICES else 'macOS 서비스',
                    'state': proc, 'always': not intervals,
                    'days': days if times else None, 'monthday': None, 'date': None,
                    'times': sorted(times), 'slot_days': times, 'mode': '', 'at_load': at_load, **models})
    return out


def claude_entries(home=None):
    """Claude tasks: the app's own store (what is scheduled now), else the backup manifest; KNOWN adds skill and result."""
    import skill_catalog as cat
    backup = {j['key']: j for j in cat.jobs()}
    try:
        from state_sync import app_schedules, app_stores
        app, _ = app_schedules(app_stores(home), include_model=True)
    except OSError:
        app = None
    if not app:
        return [{**entry, 'native': False} for entry in backup.values()]
    out = []
    for key, s in app.items():
        old = backup.get(key, {})
        known = cat.KNOWN.get(key, {})
        when = cat.cron_text(s['cron']) if s['cron'] else (f"{s['schedule'][5:7].lstrip('0')}/{s['schedule'][8:10]} {s['schedule'][11:16]} 한 번"
                                                           if s.get('schedule') else '')
        out.append({**old, 'key': key, 'name': s['title'] or old.get('name') or key, 'runner': 'Claude 예약 작업', 'when': when,
                    'skill': known.get('skill', old.get('skill', '')), 'result': old.get('result') or known.get('result') or description(key),
                    'enabled': s['enabled'], 'cron': s['cron'], 'schedule': s['schedule'], 'permission_mode': s['permission_mode'],
                    'native': True, 'model': s['model']})
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
    from schedule_history import claude_declared_model
    for e in claude_entries(home):
        shape = {'days': None, 'monthday': None, 'date': None, 'times': []}
        if e.get('cron'):
            try:
                shape = cron_job(e['cron'])
            except ValueError:
                pass
        elif (at := timestamp(e.get('schedule'))) is not None:
            shape = {'days': None, 'monthday': None, 'date': (at.year, at.month, at.day), 'times': [(at.hour, at.minute)]}
        declared = claude_declared_model(e['key'], home)
        out.append({'kind': KINDS[1], 'key': e['key'], 'name': e['name'], 'what': e.get('result') or '',
                    'state': cat.job_state(e, home) if e['native'] else '백업', 'always': False, 'mode': e.get('permission_mode') or '',
                    'skill': e.get('skill', ''), 'native': e['native'], 'enabled': e.get('enabled'), 'provider': 'Claude',
                    'model': e.get('model') or (f'{declared} (작업 선언)' if declared else '앱 기본 모델'), 'cron': e.get('cron'),
                    'when': cron_text(shape) if e.get('cron') and shape['times'] else e['when'] or '일정 확인 필요', **shape})
    return out


def _codex_records(base):
    path = base / 'sqlite/codex.db'
    if not path.is_file():
        return {}
    from contextlib import closing
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.5)) as db:
            db.row_factory = sqlite3.Row
            return {row['id']: dict(row) for row in db.execute('SELECT * FROM automations')}
    except sqlite3.Error:
        return {}


def _rule_zone(raw, parts, recorded):
    """Desktop generations have stored both local wall time and UTC rules."""
    if re.search(r'DTSTART[^\n]*Z(?:\n|$)', raw) or parts.get('UNTIL', '').endswith('Z'):
        return timezone.utc
    nominal = timestamp(recorded.get('next_run_nominal_at'))
    last = timestamp(recorded.get('last_run_at'))
    hours = {int(h) for h in parts.get('BYHOUR', '').split(',') if h.isdigit()}
    if nominal and hours and nominal.hour not in hours and nominal.astimezone(timezone.utc).hour in hours:
        return timezone.utc
    if not nominal and last and hours and last.hour not in hours and last.astimezone(timezone.utc).hour in hours:
        return timezone.utc
    return KST


def _codex_calendar(raw, entry, recorded):
    from dateutil.rrule import rrulestr

    parts = {key: value for part in raw.splitlines()[-1].removeprefix('RRULE:').split(';') if '=' in part
             for key, value in [part.split('=', 1)]}
    zone = _rule_zone(raw, parts, recorded)
    created = timestamp(recorded.get('created_at') or entry.get('created_at'))
    anchor = created.astimezone(zone) if created else datetime(2000, 1, 3, tzinfo=zone)
    default = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    if parts.get('FREQ') == 'HOURLY' and 'BYHOUR' not in parts:
        default = default.replace(hour=anchor.hour)
    try:
        rule = rrulestr(raw, dtstart=default, forceset=True)
        rule.after(default, inc=True)
    except (ValueError, TypeError, OverflowError):
        rule = None
    days = frozenset(RRULE_DAYS[d] for d in parts.get('BYDAY', '').split(',') if d in RRULE_DAYS)
    hours = [int(h) for h in parts.get('BYHOUR', '').split(',') if h.isdigit()]
    minutes = [int(m) for m in parts.get('BYMINUTE', '0').split(',') if m.isdigit()]
    grouped = {}
    monday = datetime(2000, 1, 3, tzinfo=zone)
    for day in sorted(days or range(7)):
        for h in hours:
            for m in minutes:
                if 0 <= h < 24 and 0 <= m < 60:
                    at = (monday + timedelta(days=day, hours=h, minutes=m)).astimezone(KST)
                    grouped.setdefault(at.weekday(), set()).add((at.hour, at.minute))
    by_time = {}
    for day, slots in grouped.items():
        by_time.setdefault(tuple(sorted(slots)), set()).add(day)
    ordered = sorted(by_time.items(), key=lambda pair: min(pair[1]))
    by_month = parts.get('BYMONTHDAY')
    if by_month and hours and len(ordered) == 1 and zone is KST:
        label = f'매월 {by_month}일 {times_text(ordered[0][0])}'
    elif ordered:
        label = ' · '.join(f'{days_text(ds)} {times_text(ts)}' for ts, ds in ordered)
    else:
        freq = {'HOURLY': '시간', 'DAILY': '일', 'WEEKLY': '주', 'MONTHLY': '개월'}.get(parts.get('FREQ'))
        label = f"{parts.get('INTERVAL', '1')}{freq}마다" if freq else '일정 확인 필요'
    if parts.get('INTERVAL', '1') != '1' and ordered:
        label += f" · 간격 {parts['INTERVAL']}"
    if parts.get('UNTIL'):
        try:
            end = datetime.strptime(parts['UNTIL'].rstrip('Z'), '%Y%m%dT%H%M%S').replace(tzinfo=zone).astimezone(KST)
            label += f' · {end:%m/%d %H:%M}까지'
        except ValueError:
            pass
    return rule, label, frozenset(grouped), sorted({hm for ts in grouped.values() for hm in ts})


def codex_jobs(home=None, repo=REPO):
    """Codex TOMLs identify this workspace, the scheduler DB supplies its live state."""
    base = Path(home or Path.home()) / '.codex' if home else Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    records = _codex_records(base)
    entries = {}
    for path in sorted((base / 'automations').glob('*/automation.toml')):
        try:
            entries[path.parent.name] = tomllib.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
    for key, row in records.items():
        if key not in entries:
            try:
                entries[key] = {**row, 'cwds': json.loads(row['cwds'])}
            except (ValueError, TypeError, KeyError):
                continue
    out = []
    for key, entry in entries.items():
        if not (any(str(cwd) == str(repo) or str(cwd).startswith(str(repo) + '/') for cwd in entry.get('cwds', []))
                or re.search(re.escape(str(repo)) + r'(?![A-Za-z0-9_.-])', entry.get('prompt') or '')):
            continue
        recorded = records.get(key, {})
        status = recorded.get('status') or entry.get('status')
        raw = recorded.get('rrule') or entry.get('rrule') or ''
        rule, when, days, times = _codex_calendar(raw or 'FREQ=DAILY;COUNT=0', entry, recorded)
        model = recorded.get('model') or entry.get('model')
        default = '채팅 모델 상속' if (recorded.get('kind') or entry.get('kind')) == 'heartbeat' else '앱 기본 모델'
        out.append({'kind': KINDS[2], 'key': key, 'name': entry.get('name') or key, 'when': when,
                    'what': '', 'state': {'ACTIVE': '등록됨', 'PAUSED': '일시 정지'}.get(status, '확인 불가'),
                    'always': False, 'mode': recorded.get('reasoning_effort') or entry.get('reasoning_effort') or '',
                    'days': days if rule else frozenset(), 'monthday': None, 'date': None, 'calendar': rule,
                    'next_run': timestamp(recorded.get('next_run_nominal_at') or recorded.get('next_run_at')),
                    'times': times, 'model': model or default, 'provider': 'Codex'})
    return out


def all_jobs(states=None, home=None, runtime=None):
    from schedule_dispatch import route_rows
    from schedule_history import native_history
    from schedule_imports import catalog
    from schedule_models import last_model_use

    root = _nhplug(runtime, home)
    jobs = launchd_jobs(states, home, runtime) + catalog(runtime, claude_jobs(home)) + codex_jobs(home)
    native = native_history(jobs, home=home, runtime=root)
    routes, runs = route_rows(runtime, jobs=[j for j in jobs if j['kind'] == KINDS[1]],
                             codex=[j for j in jobs if j['kind'] == KINDS[2]])
    now = datetime.now(KST)
    for job in jobs:
        if job['kind'] == KINDS[1] and (route := routes.get(job['key'])):
            job['route'] = route
            blocked = job.get('enabled') or not job.get('native') or job['cron'] != route['cron'] or not route['ready']
            job['route_state'] = '전환 대기' if blocked else '예약 실행'
            if not job.get('enabled'):
                job.update(provider=route['provider'], model=route['model'], state='전환 대기' if blocked else '등록됨')
        rows = native.get((job['kind'], job['key']), [])
        if job['kind'] == KINDS[1]:
            rows = rows + runs.get(job['key'], [])
        if rows:
            last = max(rows, key=_run_sort)
            job.update(last_run=last.get('started'), last_observed=last.get('observed'),
                       last_model=last.get('model'), last_status=last.get('status'))
        if job['kind'] == KINDS[0]:
            service = job['key'].removeprefix('com.vibetrading.')
            if usage := last_model_use(service, root, Path(home or Path.home())):
                model, observed, source = usage
                job.update(last_model=model, last_model_at=timestamp(observed), last_model_source=source)
        if job['state'] not in NON_ACTIVE and not job.get('next_run'):
            job['next_run'] = job_next_start(job, now)
    return jobs


def _nhplug(runtime, home=None):
    from state_sync import runtime_root
    return runtime_root(runtime or (Path(home) / '.vibe-trading' if home is not None else None)) / 'nhplug'


def _run_sort(row):
    return row.get('started') or row.get('observed') or datetime.min.replace(tzinfo=KST)


def job_history(job, runtime=None, home=None):
    from schedule_dispatch import route_history
    from schedule_history import native_history
    key = job['kind'], job['key']
    rows = native_history([job], home=home, runtime=_nhplug(runtime, home), detailed=True).get(key, [])
    if job['kind'] == KINDS[1]:
        rows += route_history(runtime, job['key'])
    return sorted(rows, key=_run_sort, reverse=True)


def job_next_start(job, now):
    if job['state'] in NON_ACTIVE:
        return None
    if (rule := job.get('calendar')) is not None:
        at = rule.after(now)
        return at.astimezone(KST) if at else None
    for offset in range(32):
        day = (now + timedelta(days=offset)).date()
        for h, m in starts_on(job, day):
            at = datetime.combine(day, time(h, m), now.tzinfo)
            if at > now:
                return at
    return None


def next_start(jobs, now):
    """(datetime, job) for the next scheduled start."""
    future = []
    for job in jobs:
        if job['state'] not in NON_ACTIVE and (at := job.get('next_run') or job_next_start(job, now)) and at > now:
            future.append((at, job))
    return min(future, key=lambda pair: (pair[0], pair[1]['name']), default=None)

