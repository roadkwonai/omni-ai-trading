"""API 부하·분산 감사 (user 2026-11-24): are the external APIs shared out well, is any of them called too much, is any refused?

Reads only the call log of scripts/api_calls.py (10-minute slots, failures, calls started per second). No API call, no
model, no order; nothing is changed. It finds facts and names the usual remedy; applying one stays with the user.

    PYTHONPATH=agent:scripts .venv/bin/python scripts/api_load.py            # the report of today so far
    ... --watch                                                              # the launchd service: every minute until 15:40,
        new findings to the realtime Discord channel, then reviews/api-audit-<day>.md
`intraday_audit.collect` takes the same findings every hour.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

import api_calls
import api_catalog as cat
from config_store import write_json, write_text
from sqlite_ro import ro_connect

KST = api_calls.KST
WINDOW_MIN = 30
BASE_DAYS = 5      # earlier log days the usual speed and volume come from
REFUSED = 3        # refusals in the window; one or two clear on the caller's own retry
LOCAL_STARTS = 2
FAILS, FAIL_SHARE = 5, 0.2
SLOW_MS, SLOW_TIMES, SLOW_CALLS = 1500, 2, 20
SURGE_TIMES, SURGE_CALLS, SURGE_FROM = 2, 150, '09:35'  # the open's volume moves with every start-time change
BURST_SECONDS = 3  # the log counts a call in the second it started, NH a moment later: one second at the limit proves little
REPEAT_MIN = 30    # a finding gone this long is reported again when it returns
END = '15:40'
LIMITS = {'nh': (5, False), 'kiwoom': (5, True)}  # calls a second: of the app key / of each TR (docs/broker-data-sources-research.md)
ADVICE = {
    'refused': '같은 조회를 여러 프로그램이 겹쳐 부르는지 보고, 공유 기록·캐시를 읽게 하거나 조회 간격을 벌립니다',
    'local_start': 'NH에 닿기 전 로컬 MCP 프로세스가 늦게 뜬 것입니다. 무거운 작업(전체 테스트·렌더링)을 장중에 피하고, MCP 연결 재사용을 검토합니다',
    'failing': '제공처 장애인지 먼저 확인합니다(다른 API가 정상이면 제공처 쪽). 대체 출처가 있으면 그 출처로 넘어갔는지 봅니다',
    'slow': '제공처가 느린지 Mac이 느린지 나눠 봅니다(다른 API도 느리면 Mac). 겹치는 조회를 줄이고 무거운 작업을 멈춥니다',
    'surge': '그 프로그램의 조회 주기·재시도·화면 자동 갱신이 늘었는지 보고, 같은 값을 다른 프로그램 기록에서 읽을 수 있는지 봅니다',
    'burst': '같은 초에 몰리는 프로그램의 시작 시각을 어긋나게 하거나 공유 관문(nh_budget) 몫을 조정합니다',
}


def _rows(path, sql, args=()):
    """[] while the log is missing or its table is not there yet (older code wrote it)."""
    if not Path(path).exists():
        return []
    try:
        with ro_connect(path, timeout=1) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(sql, args)]
    except sqlite3.Error:
        return []


def kind_of(row):
    """'refused' (the provider's own limit) | 'local_start' (nothing reached NH) | 'failing'."""
    detail = row['detail'] or ''
    if row['status'] == 429 or 'rate_limit' in detail or 'IGW4290' in detail:
        return 'refused'
    if row['provider'] == 'nh' and detail in (api_calls.LOCAL_START, 'RuntimeError'):  # RuntimeError: rows before 2026-11-24
        return 'local_start'
    return 'failing'


def collect(root, now):
    path, day, since = Path(root) / 'api-calls.sqlite3', f'{now:%Y-%m-%d}', now - timedelta(minutes=WINDOW_MIN)
    today = _rows(path, 'SELECT * FROM calls WHERE day = ? AND slot <= ?', (day, f'{now:%H:%M}'))
    slots = sorted({r['slot'] for r in today if r['slot'] >= f'{since:%H:%M}'})
    days = [r['day'] for r in _rows(path, 'SELECT DISTINCT day FROM calls WHERE day < ? AND slot BETWEEN ? AND ? ORDER BY day DESC LIMIT ?',
                                    (day, '09:00', '15:20', BASE_DAYS))]
    base = _rows(path, f"SELECT * FROM calls WHERE day IN ({','.join('?' * len(days))})", days) if days else []
    stamp = now.isoformat(timespec='seconds')
    return {'clock': f'{now:%H:%M}', 'today': today, 'slots': slots, 'days': days, 'base': base,
            'fails': _rows(path, 'SELECT * FROM failures WHERE time >= ? AND time <= ?', (day, stamp)), 'since': since.isoformat(timespec='seconds'),
            'seconds': _rows(path, 'SELECT * FROM seconds WHERE time >= ? AND time <= ?', (day, stamp))}


def peaks(rows):
    """{provider: calls started in each second} — for a provider limited per TR, the busiest TR of that second."""
    by = Counter()
    for r in rows:
        by[(r['provider'], r['time'], r['endpoint'] if LIMITS.get(r['provider'], (0, False))[1] else '')] += r['n']
    out = {}
    for (provider, second, _), n in by.items():
        seconds = out.setdefault(provider, {})
        seconds[second] = max(n, seconds.get(second, 0))
    return out


def speed(rows):
    n = sum(r['n'] for r in rows)
    return n, sum(r['ms_sum'] for r in rows) / n if n else None


def usual_ms(f, provider, slots=None):
    """Median over the base days of that day's mean response time (same slots when given)."""
    means = [ms for day in f['days'] if (ms := speed([r for r in f['base'] if r['day'] == day and r['provider'] == provider
                                                      and (slots is None or r['slot'] in slots)])[1]) is not None]
    return median(means) if means else None


def who(rows, key='caller'):
    return ', '.join(f"{cat.CALLERS.get(k, k)} {n}" for k, n in Counter(r[key] for r in rows).most_common(3))


def findings(f):
    """[{'key', 'detail', 'advice'}] of the last WINDOW_MIN minutes."""
    out = []

    def add(kind, provider, detail, tail=''):
        out.append({'key': f'api.{kind}:{provider}{tail}', 'detail': f'{cat.name_of(provider)} {detail}', 'advice': ADVICE[kind]})

    recent = [r for r in f['today'] if r['slot'] in f['slots']]
    groups = {}
    for r in f['fails']:
        if r['time'] >= f['since']:
            groups.setdefault((r['provider'], kind_of(r)), []).append(r)
    for (provider, kind), rows in groups.items():
        n = len(rows)
        if kind == 'refused' and n >= REFUSED:
            codes = ', '.join(f'{k} {v}건' for k, v in Counter(r['detail'].removeprefix('tool error: rate_limit ') for r in rows).most_common())
            add(kind, provider, f'최근 {WINDOW_MIN}분 호출 제한 거절 {n}건 ({codes}) · {who(rows)}')
        elif kind == 'local_start' and n >= LOCAL_STARTS:
            add(kind, provider, f'최근 {WINDOW_MIN}분 로컬 연결 시작 실패 {n}건 · {who(rows)}')
        elif kind == 'failing' and n >= FAILS and n >= FAIL_SHARE * speed([r for r in recent if r['provider'] == provider])[0]:
            add(kind, provider, f"최근 {WINDOW_MIN}분 실패 {n}건 ({who(rows, 'detail')}) · {who(rows)}")
    for provider in sorted({r['provider'] for r in recent}):
        n, ms = speed([r for r in recent if r['provider'] == provider])
        usual = usual_ms(f, provider)
        if n >= SLOW_CALLS and ms >= SLOW_MS and usual and ms >= SLOW_TIMES * usual:
            add('slow', provider, f'최근 {WINDOW_MIN}분 평균 응답 {ms:,.0f}ms (평소 {usual:,.0f}ms)')
    volume = Counter()
    for r in recent:
        volume[(r['provider'], r['caller'])] += r['n']
    for (provider, caller), n in volume.items() if f['clock'] >= SURGE_FROM else ():
        usual = median([sum(r['n'] for r in f['base'] if r['day'] == day and r['slot'] in f['slots'] and (r['provider'], r['caller']) == (provider, caller))
                        for day in f['days']]) if f['days'] else None
        if usual is not None and n >= SURGE_TIMES * usual and n - usual >= SURGE_CALLS:
            add('surge', provider, f'{cat.CALLERS.get(caller, caller)} 호출 {n}건 (같은 시간대 평소 {usual:,.0f}건)', f':{caller}')
    for provider, seconds in peaks([r for r in f['seconds'] if r['time'] >= f['since']]).items():
        limit = LIMITS.get(provider, (None,))[0]
        hits = [s for s, n in seconds.items() if limit and n >= limit]
        if len(hits) >= BURST_SECONDS:
            add('burst', provider, f'최근 {WINDOW_MIN}분에 초당 한도 {limit}회에 닿은 초 {len(hits)}번 (최대 {max(seconds.values())}회)')
    return out


def table(f):
    """One row per API called today, busiest first (report and dashboard)."""
    top, out = peaks(f['seconds']), []
    for provider, n in Counter({p: sum(r['n'] for r in f['today'] if r['provider'] == p) for p in {r['provider'] for r in f['today']}}).most_common():
        rows = [r for r in f['today'] if r['provider'] == provider]
        slots, callers = Counter(), Counter()
        for r in rows:
            slots[r['slot']] += r['n']
            callers[r['caller']] += r['n']
        slot, busiest = slots.most_common(1)[0]
        kinds = Counter(kind_of(r) for r in f['fails'] if r['provider'] == provider)
        limit = LIMITS.get(provider, (None, False))
        timed = sum(r['n'] for r in f['seconds'] if r['provider'] == provider)  # processes started with older code are not in it
        out.append({'API': cat.name_of(provider), '호출': n, '가장 바쁜 10분': f'{slot} · {busiest}건(초당 {busiest / 600:.2f})',
                    '초당 최대': f"{max(top[provider].values())}회{'' if timed >= 0.9 * n else ' (일부)'}" if timed else '측정 전',
                    '초당 한도': f"{limit[0]}회{'(TR별)' if limit[1] else ''}" if limit[0] else '—',
                    '거절': kinds['refused'], '로컬 시작 실패': kinds['local_start'], '그 밖의 실패': kinds['failing'],
                    '평균 응답(ms)': speed(rows)[1], '평소(ms)': usual_ms(f, provider),
                    '호출 프로그램': ' · '.join(f'{cat.CALLERS.get(c, c)} {k / n:.0%}' for c, k in callers.most_common(3))})
    return out


def blocked(f):
    """What was blocked or failed today, by API, kind, program and request."""
    names = {'refused': '호출 제한 거절', 'local_start': '로컬 시작 실패', 'failing': '그 밖의 실패'}
    by = {}
    for r in f['fails']:
        by.setdefault((r['provider'], kind_of(r), r['caller'], r['endpoint'].removeprefix('mcp:'), r['detail']), []).append(r['time'])
    return [{'API': cat.name_of(p), '구분': names[k], '프로그램': cat.CALLERS.get(c, c), '조회': e, '내용': d, '건수': len(t),
             '시각': f'{min(t)[11:16]}~{max(t)[11:16]}'} for (p, k, c, e, d), t in sorted(by.items(), key=lambda x: -len(x[1]))]


def spread(f, provider='nh'):
    """Which requests take one provider's calls: where sharing out would help most."""
    by = Counter()
    for r in f['today']:
        if r['provider'] == provider:
            by[(r['caller'], r['endpoint'].removeprefix('mcp:'))] += r['n']
    total = sum(by.values())
    return [{'프로그램': cat.CALLERS.get(c, c), '조회': e, '호출': n, '비중': f'{n / total:.0%}'} for (c, e), n in by.most_common(8)]


def _state_path(root):
    return Path(root) / 'shared-etf-paper' / 'api-watch-state.json'


def seen(root, now):
    """{key: {'first', 'last', 'detail', 'advice'}} the watch reported today."""
    try:
        saved = json.loads(_state_path(root).read_text(encoding='utf-8'))
        return saved['seen'] if saved['day'] == f'{now:%Y-%m-%d}' else {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def step(send, root, now):
    """One round: what is new (or back after REPEAT_MIN) goes out, and counts as reported only once it arrived."""
    found, known, stamp = findings(collect(root, now)), seen(root, now), now.isoformat(timespec='seconds')
    new = [x for x in found if x['key'] not in known or now - datetime.fromisoformat(known[x['key']]['last']) > timedelta(minutes=REPEAT_MIN)]
    if new and not send(f'📡 API 부하 감사 · {now:%m-%d %H:%M}\n' + '\n'.join(f"⚠️ {x['detail']}\n→ {x['advice']}" for x in new)):
        fresh = {x['key'] for x in new}
        found = [x for x in found if x['key'] not in fresh]
    for x in found:
        known[x['key']] = {'first': known.get(x['key'], {}).get('first', stamp), 'last': stamp, 'detail': x['detail'], 'advice': x['advice']}
    write_json(_state_path(root), {'day': f'{now:%Y-%m-%d}', 'seen': known})
    return found


def _md(rows):
    if not rows:
        return '없음\n'
    cell = lambda v: '아직 없음' if v is None else f'{v:,.0f}' if isinstance(v, float) else f'{v:,}' if isinstance(v, int) else str(v).replace('|', '/')  # noqa: E731
    head = list(rows[0])
    return '\n'.join(['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head), *('| ' + ' | '.join(cell(r[h]) for h in head) + ' |' for r in rows)]) + '\n'


def report(root, now):
    f = collect(root, now)
    known = dict(seen(root, now))
    for x in findings(f):
        known.setdefault(x['key'], {'first': now.isoformat(timespec='seconds'), 'last': now.isoformat(timespec='seconds'), **x})
    total, lost = sum(r['n'] for r in f['today']), Counter(kind_of(r) for r in f['fails'])
    found = [f"- **{v['detail']}** (`{k}`, {v['first'][11:16]}~{v['last'][11:16]})" for k, v in sorted(known.items())]
    advice = {v['advice']: [] for v in known.values()}
    for k, v in sorted(known.items()):
        advice[v['advice']].append(cat.name_of(k.split(':')[1]))
    return '\n'.join([
        f'# API 부하·분산 감사 {now:%Y-%m-%d}', '',
        f'- 기준 {now:%H:%M} KST · 호출 {total:,}건 · 호출 제한 거절 {lost["refused"]}건 · 로컬 시작 실패 {lost["local_start"]}건 · 그 밖의 실패 {lost["failing"]}건',
        f'- 평소 = 앞선 {len(f["days"])}일의 중앙값. 호출 기록만 읽었고 API를 부르거나 설정을 바꾸지 않았습니다.', '',
        '## API별 현황', '', _md(table(f)),
        '## 막힌 요청', '', _md(blocked(f)),
        '## NH 호출을 많이 쓰는 조회', '', _md(spread(f)),
        '## 오늘 찾은 문제', '', *(found or ['없음']), '',
        '## 보완 제안', '', *([f"- {text} ({', '.join(dict.fromkeys(names))})" for text, names in advice.items()] or ['없음']), '',
        '제안만 합니다. 적용은 사용자 승인 뒤에 합니다.', ''])


def write_report(root, now):
    path = Path(root) / 'shared-etf-paper' / 'reviews' / f'api-audit-{now:%Y-%m-%d}.md'
    write_text(path, report(root, now))
    return path


def discord(text):
    """One message to the realtime Discord channel; True when it arrived."""
    import discord_notify as D
    targets = [t for t in D.urls_from_env().get('realtime') or [] if t[0] == 'discord']
    return bool(targets) and all(D.send_everywhere(targets, text).values())


def watch(root, end=END, clock=lambda: datetime.now(KST), sleep=time.sleep, send=discord):
    while (now := clock()).strftime('%H:%M') < end:
        if now.strftime('%H:%M') >= '09:05':
            try:
                step(send, root, now)
            except Exception as exc:  # noqa: BLE001 - a busy log must not end the watch
                print(f'{now:%H:%M} {type(exc).__name__}', flush=True)
        sleep(60)
    path, known = write_report(root, now), seen(root, now)
    send(f'📡 API 부하 감사 마감 · {now:%m-%d %H:%M} · 오늘 찾은 문제 {len(known)}건' + ''.join(f"\n- {v['detail']}" for v in known.values())
         + f'\n상세: 운영 → API 연동 ({path.name})')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    root, now = api_calls.DB.parent, datetime.now(KST)
    if not args.watch:
        print(report(root, now))
        return
    from vibe_longterm import trading_day
    if trading_day(now.date()) and now.strftime('%H:%M') < END:
        watch(root)


if __name__ == '__main__':
    main()
