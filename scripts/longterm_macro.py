"""Rates, credit and the US release calendar from FRED for the long-term engine (user 2026-11-17, P1 of
docs/longterm-external-skills-review.md).

Reference input only: vibe_longterm.decide() records it (decisions kind 'macro'), passes it to the theme story check and
the strategy report; the regime, the module targets and the orders never read it. Any failure drops that input alone.
The key comes from the repository .env (FRED_API_KEY) and is never logged: errors keep the exception type only.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date, timedelta

BASE = 'https://api.stlouisfed.org/fred/'
FOMC_PAGE = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'  # FRED lists 'FOMC Press Release' every day
# series id: (label, unit)
SERIES = {'T10Y2Y': ('10년−2년 금리차', '%p'), 'DGS10': ('미 10년 국채금리', '%'),
          'DFII10': ('미 10년 실질금리', '%'), 'BAMLH0A0HYM2': ('하이일드 스프레드', '%p')}
# FRED release name: label (the releases that move rates and US equities)
EVENTS = {'Consumer Price Index': 'CPI', 'Employment Situation': '고용',
          'Gross Domestic Product': 'GDP', 'Personal Income and Outlays': 'PCE 물가', 'Producer Price Index': 'PPI'}
STALE_DAYS = 10    # daily series lag a day or two; older than this is not today's picture
MONTH_OBS = 21     # about one month of trading days
CALENDAR_DAYS = 30  # about a month ahead: FOMC meets roughly every six weeks


def _key():
    from dotenv import dotenv_values
    from local_env import ENV
    return dotenv_values(ENV, interpolate=False).get('FRED_API_KEY') or ''


def _get(key):
    def get(path, **params):
        query = urllib.parse.urlencode({**params, 'api_key': key, 'file_type': 'json'})
        with urllib.request.urlopen(f'{BASE}{path}?{query}', timeout=20) as r:
            return json.load(r)
    return get


def series(sid, today, get):
    rows = [(date.fromisoformat(o['date']), float(o['value'])) for o in
            get('series/observations', series_id=sid, observation_start=(today - timedelta(days=400)).isoformat())['observations']
            if o.get('value') not in (None, '', '.')]
    if not rows:
        raise ValueError('no observations')
    last_day, last = rows[-1]
    year = [v for d, v in rows if d >= last_day - timedelta(days=365)]
    label, unit = SERIES[sid]
    return {'label': label, 'unit': unit, 'date': last_day.isoformat(), 'value': last,
            'chg_1m': last - rows[-1 - MONTH_OBS][1] if len(rows) > MONTH_OBS else None,
            'low_1y': min(year), 'high_1y': max(year), 'stale': (today - last_day).days > STALE_DAYS}


def calendar(today, get, days=CALENDAR_DAYS):
    rows = get('releases/dates', realtime_start=today.isoformat(), realtime_end=(today + timedelta(days=days)).isoformat(),
               include_release_dates_with_no_data='true', sort_order='asc', limit=1000)['release_dates']
    end = (today + timedelta(days=days)).isoformat()
    found = {(r['date'], EVENTS[r['release_name']]) for r in rows
             if r.get('release_name') in EVENTS and today.isoformat() <= r.get('date', '') <= end}
    return [{'date': d, 'label': label} for d, label in sorted(found)]


MONTHS = {m: i + 1 for i, m in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'))}


def fomc_days(html):
    """Announcement days (the last day of each scheduled meeting) from the Fed's FOMC calendar page; notation votes
    and unscheduled meetings are left out."""
    out, year, month = [], None, None
    for m in re.finditer(r'(\d{4}) FOMC Meetings|fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>|fomc-meeting__date[^>]*>([^<]+)<', html):
        if m.group(1):
            year = int(m.group(1))
        elif m.group(2):
            month = MONTHS.get(m.group(2).strip().split('/')[-1][:3].lower())
        elif year and month and re.fullmatch(r'\d{1,2}(-\d{1,2})?\*?', m.group(3).strip()):
            out.append(date(year, month, int(re.findall(r'\d+', m.group(3))[-1])))
    return sorted(out)


def _page():
    request = urllib.request.Request(FOMC_PAGE, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=20) as r:
        return r.read().decode('utf-8', 'replace')


def collect(today, get=None, key=None, page=None):
    """{'as_of', 'series': {id: facts}, 'events': [{date, label}], 'errors': [...]}; never raises."""
    key = _key() if key is None else key
    out = {'as_of': today.isoformat(), 'source': 'FRED · 연준', 'series': {}, 'events': [], 'errors': []}
    try:
        out['events'] = [{'date': d.isoformat(), 'label': 'FOMC'} for d in fomc_days((page or _page)())
                         if today <= d <= today + timedelta(days=CALENDAR_DAYS)]
    except Exception as exc:
        out['errors'].append(f'fomc: {type(exc).__name__}')
    if not key:
        out['errors'].append('FRED_API_KEY 없음')
        return out
    get = get or _get(key)
    for sid in SERIES:
        try:
            out['series'][sid] = series(sid, today, get)
        except Exception as exc:
            out['errors'].append(f'{sid}: {type(exc).__name__}')
    try:
        out['events'] = sorted(out['events'] + calendar(today, get), key=lambda e: (e['date'], e['label']))
    except Exception as exc:
        out['errors'].append(f'calendar: {type(exc).__name__}')
    return out


def brief(m):
    """One Korean line per fact for prompts and the report: value, one-month change, one-year range, date."""
    lines = [f"{s['label']} {s['value']:.2f}{s['unit']} (1개월 {s['chg_1m']:+.2f}, 1년 {s['low_1y']:.2f}~{s['high_1y']:.2f}, "
             f"{s['date']}{', 오래됨' if s['stale'] else ''})" if s.get('chg_1m') is not None else
             f"{s['label']} {s['value']:.2f}{s['unit']} ({s['date']})" for s in (m.get('series') or {}).values()]
    if m.get('events'):
        lines.append('다가오는 발표: ' + ', '.join(f"{e['label']} {e['date']}" for e in m['events']))
    if not lines:
        return f"거시 자료 없음({', '.join(m.get('errors') or ['이유 모름'])})"
    return '\n'.join(lines)


if __name__ == '__main__':
    print(json.dumps(collect(date.today()), ensure_ascii=False, indent=1))
