"""장기투자 장중 상시 감시 (user request 2026-11-24 "장중 상시 감사"): reads the records the long-term engine, the long-term
live account and the shared collectors already leave, and prints what is blocked, missing, late or stale. No model, no
broker call and no order, so a usage limit does not stop it. Read-only except its own --state file.

    PYTHONPATH=agent:scripts .venv/bin/python scripts/longterm_watch.py            # one check, every finding
    ... --until-change --state .scratch/longterm-watch.json                        # every minute; exits on the first change
    ... --watch                                                                    # the launchd service: changes go to Discord
It finds facts only; fixing and any setting stay with the user and the audits (longterm_audit, intraday_audit).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
NHPLUG = Path.home() / '.vibe-trading' / 'nhplug'
TOPICS = {'fed': '연준', 'us_macro': '미국 거시', 'semi': '반도체', 'semi_policy': '반도체 정책', 'quantum': '양자', 'space': '우주'}  # longterm_news
MACRO = ('T10Y2Y', 'DGS10', 'DFII10', 'BAMLH0A0HYM2')  # longterm_macro
STEPS = (('08:55', ('regime',)), ('08:55', ('macro',)), ('08:55', ('news',)), ('08:55', ('story',)), ('08:55', ('map',)), ('09:50', ('plan', 'order')))
CLOSE_BY, REPORT_BY, PLAN_BY = '15:50', '09:30', '09:50'
SERVICES = {'vibe-longterm': ('08:50', '15:40'), 'longterm-live-2': ('09:37', '14:30'), 'discord-notify': ('08:30', '23:59')}
CALLERS = ('vibe_longterm', 'longterm_live')
API_FAILS = 3  # in the last hour; fewer clear on their own retry
BLOCKED = 3  # the same order refused this many rounds in a row, or minutes: the executor writes a repeated reason once
STORY_OLD_DAYS = 30
LOOK_MIN = 5  # longterm_dca.AUTO_CHECK_MIN: the minutes the 적립식 reads quotes at
SENT = ('ACKNOWLEDGED_NOT_FILLED', 'SENT_BEFORE_RESTART')


def _json(text):
    try:
        return json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}


def _rows(path, sql, args=()):
    from sqlite_ro import ro_connect
    if not Path(path).exists():
        return []
    with ro_connect(path) as db:
        return db.execute(sql, args).fetchall()


def us_sessions(then, today):
    """Weekdays from `then` (a US close) to the last one finished before this Korean morning; US holidays count as one."""
    n, d = 0, then + timedelta(days=1)
    while d < today:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


def collect(root=NHPLUG, now=None, run=subprocess.run, halted=None):
    root, now = Path(root), now or datetime.now(KST)
    day, engine = now.date().isoformat(), root / 'vibe-longterm'
    decisions = [{'rowid': i, 'time': t, 'kind': k, 'reason': r or '', 'indicators': _json(ind), 'targets': _json(tg)}
                 for i, t, k, r, ind, tg in _rows(engine / 'longterm.sqlite3', 'SELECT rowid,time,kind,reason,indicators,targets '
                                                  'FROM decisions WHERE day=? ORDER BY rowid', (day,))]
    from sqlite_ro import day_rows
    live = [r for r in day_rows(root / 'longterm-live.jsonl', day)[0] or [] if r.get('account') == 'live-2']
    if halted is None:
        from src.live.halt import halt_flag_set
        halted = lambda: halt_flag_set('nh')  # noqa: E731
    stop = [str(p.relative_to(root)) for p in (engine / 'STOP', root / 'STOP') if p.exists()] + (['NH HALT'] if halted() else [])
    services = {}
    for name in SERVICES:
        out = run(['launchctl', 'list', f'com.vibetrading.{name}'], capture_output=True, text=True)
        services[name] = out.returncode == 0 and bool(re.search(r'"PID" = \d+;', out.stdout))
    accounts = _json(_text(root / 'accounts.json')).get('accounts') or {}
    record = accounts.get('live-2') if isinstance(accounts, dict) else next((a for a in accounts if a.get('key') == 'live-2'), None)
    hour_ago = (now - timedelta(hours=1)).isoformat()
    recovered = {}  # step -> its last result that was no error (the engine's own log; it names the step of each error)
    for line in _text(engine / 'com.vibetrading.vibe-longterm.log').splitlines()[-2000:]:
        r = _json(line) if line.startswith('{"time": "' + day) else {}
        if not r.get('step') or str(r.get('status')).startswith('error'):
            continue
        at = datetime.fromisoformat(r['time'])
        if r['step'] == 'dca' and r['status'] == 'waiting' and at.minute % LOOK_MIN:  # no quote read at this minute
            at = at.replace(second=0, microsecond=0) + timedelta(minutes=LOOK_MIN - at.minute % LOOK_MIN)
            if now < at + timedelta(minutes=2):  # its next look has not passed yet; an error there would write its own line
                continue
        recovered[r['step']] = at.isoformat()
    marks = ','.join('?' * len(CALLERS))
    return {'decisions': decisions, 'live': live, 'live_on': bool((record or {}).get('live_orders')), 'stop': stop, 'services': services,
            'failures': _rows(root / 'api-calls.sqlite3', f'SELECT time,provider,endpoint,caller,kind FROM failures WHERE time>=? '
                              f'AND caller IN ({marks})', (hour_ago, *CALLERS)),
            'cursor': _json(_text(root / 'discord-notify.json')).get('lt_cursor'),
            'rejected': sum(1 for r in day_rows(root / 'balance-rejected.jsonl', day)[0] or [] if 'live-2' in json.dumps(r)),
            'report': (engine / 'reports' / f'strategy-{day}.md').exists(),
            'recovered': recovered, 'nav': bool(_rows(engine / 'longterm.sqlite3', "SELECT 1 FROM nav WHERE day=? AND book='actual'", (day,)))}


def _text(path):
    try:
        return Path(path).read_text(encoding='utf-8')
    except OSError:
        return ''


def findings(f, now):
    clock, out = now.strftime('%H:%M'), {}
    by = {}
    for r in f['decisions']:
        by.setdefault(r['kind'], []).append(r)
    if f['stop']:
        out['stop'] = f"중단 켜짐: {', '.join(f['stop'])}"
    for due, kinds in STEPS:
        if clock >= due and not any(k in by for k in kinds):
            out[f'step.{kinds[0]}'] = f"{'/'.join(kinds)} 기록이 {due}까지 없음"
    if clock >= CLOSE_BY and not f['nav']:
        out['step.close'] = f'오늘 마감 순자산이 {CLOSE_BY}까지 없음'
    for kind in ('error', 'missed'):
        for r in by.get(kind, []):
            if f.get('recovered', {}).get(r['reason'].split(':')[0], '') > r['time']:
                continue  # the next try worked: the finding clears
            out[f"engine.{kind}:{r['rowid']}"] = f"{r['time'][11:16]} {r['reason'][:160]}"
    for name, (start, end) in SERVICES.items():
        if start <= clock < end and not f['services'].get(name):
            out[f'service.{name}'] = f'{name}가 {start}~{end}에 실행 중이 아님'
    for name, r in ((k, v[-1]) for k, v in by.items()):
        ind, tg = r['indicators'], r['targets']
        if name == 'news':
            have = {x.get('topic') for x in ind.get('rows') or []}
            if missing := [label for t, label in TOPICS.items() if t not in have]:
                out['news.topic'] = f"장기 뉴스 주제 없음: {', '.join(missing)}" + (f" ({ind['error']})" if ind.get('error') else '')
        elif name == 'macro':
            series = ind.get('series') or {}
            if bad := [k for k in MACRO if k not in series or series[k].get('stale')]:
                out['macro.stale'] = f"거시 값 없음·오래됨: {', '.join(bad)}"
        elif name == 'regime' and ind.get('as_of'):
            if us_sessions(date.fromisoformat(ind['as_of']), now.date()) > 1:  # one spare for a US holiday
                out['regime.stale'] = f"국면 지표 기준일 {ind['as_of']}"
        elif name == 'story':
            if tg.get('source') and tg['source'] != 'llm':
                out['story.fallback'] = f"스토리 판정 출처 {tg['source']}"
            if tg.get('sources') == ['새 근거 없음']:  # vibe_longterm.recent_sources
                out['story.no_evidence'] = '테마 스토리 판정에 최근 30일 근거 없음'
            dates = [date.fromisoformat(d) for s in tg.get('sources') or [] for d in re.findall(r'20\d\d-[01]\d-[0-3]\d', s)]
            if dates and (old := [d for d in dates if (now.date() - d).days > STORY_OLD_DAYS]) and len(old) * 2 >= len(dates):
                out['story.old_sources'] = f"스토리 근거 {len(dates)}건 중 {len(old)}건이 {STORY_OLD_DAYS}일 넘음(가장 오래된 {min(old)})"
    _live(f, now, clock, out)
    count = {}
    for _, provider, endpoint, caller, kind in f['failures']:
        count.setdefault((provider, caller), []).append(f'{endpoint} {kind}')
    for (provider, caller), hits in count.items():
        if len(hits) >= API_FAILS:
            out[f'api.{provider}:{caller}'] = f'{caller} {provider} 최근 1시간 실패 {len(hits)}건 (마지막 {hits[-1]})'
    last = f['decisions'][-1] if f['decisions'] else None
    if last and type(f['cursor']) is int and f['cursor'] < last['rowid'] and (now - datetime.fromisoformat(last['time'])).total_seconds() > 300:
        out['discord.behind'] = f"Discord 장기 알림이 {last['rowid'] - f['cursor']}건 밀림"
    if f['rejected']:
        out['balance.rejected'] = f"실계좌 2 잘못된 잔고 {f['rejected']}건 거절"
    if clock >= REPORT_BY and not f['report']:
        out['report.missing'] = f'아침 전략 리포트가 {REPORT_BY}까지 없음'
    return out


def _live(f, now, clock, out):
    rows = f['live']
    if f['live_on'] and clock >= PLAN_BY and not any(r.get('action') == 'PLAN' for r in rows):
        out['live.no_plan'] = f'실계좌 2 계획이 {PLAN_BY}까지 없음'
    if f['live_on'] and rows and (rows[-1].get('mode') != 'execute' or not rows[-1].get('armed')):
        out['live.unarmed'] = f"실주문 스위치는 켜짐인데 실행기 mode {rows[-1].get('mode')} · armed {rows[-1].get('armed')}"
    retries, sent = {}, {}
    for r in rows:
        symbol, action = r.get('symbol'), r.get('action')
        if r.get('execution') == 'RETRY':
            retries[symbol] = retries.get(symbol, 0) + 1
        elif symbol:
            retries.pop(symbol, None)
        if r.get('execution') in SENT:
            key = (symbol, action)
            if key in sent:
                out[f'live.twice:{symbol}:{action}'] = f"{symbol} {action}를 {sent[key]}와 {r['time'][11:16]}에 두 번 보냄(취소·체결 확인 필요)"
            sent[key] = r['time'][11:16]
        elif action == 'RECONCILED' and any(v.get('cancelled') for v in (r.get('reconciled') or {}).values()):
            sent.clear()  # a cancelled order may be sent again
        if action == 'BLOCK' or r.get('execution') in ('REJECTED', 'BLOCKED'):
            out['live.refused'] = f"{r['time'][11:16]} {r.get('reason')}"
    for symbol, n in retries.items():
        r = [r for r in rows if r.get('symbol') == symbol][-1]
        stuck = r is rows[-1] and clock < '14:30' and (now - datetime.fromisoformat(r['time'])).total_seconds() >= BLOCKED * 60
        if n >= BLOCKED or stuck:
            out[f'live.blocked:{symbol}'] = f"{symbol} 주문이 {r['time'][11:16]}부터 막혀 있음: {r.get('reason')}"


def diff(found, state, now):
    """[('new' | 'resolved', key, detail)] against what `state` holds for today."""
    saved = _json(_text(state))
    known = saved.get('found') or {} if saved.get('day') == now.date().isoformat() else {}
    return [('new', k, v) for k, v in found.items() if k not in known] + [('resolved', k, v) for k, v in known.items() if k not in found]


def save(found, state, now):
    Path(state).write_text(json.dumps({'day': now.date().isoformat(), 'time': now.isoformat(), 'found': found}, ensure_ascii=False), encoding='utf-8')


def changes(found, state, now):
    out = diff(found, state, now)
    save(found, state, now)
    return out


def discord(text):
    """One message to the long-term Discord channel (user 2026-11-24); True when it arrived."""
    import discord_notify as D
    targets = [t for t in D.urls_from_env().get('longterm') or [] if t[0] == 'discord']
    return bool(targets) and all(D.send_everywhere(targets, text).values())


def mask(text):
    return re.sub(r'\d{8,}', '…', text)  # no account number, ever; stock codes are 6


def step(send, state, now, collected=None):
    """One round of the service: what changed goes out, and counts as reported only once it arrived."""
    found = findings(collected or collect(now=now), now)
    if shown := diff(found, state, now):
        lines = [f"{'⚠️' if s == 'new' else '✅ 해소'} {text}" for s, _, text in shown]
        text = mask(f'🔎 장기투자 감시 · {now:%m-%d %H:%M}\n' + '\n'.join(lines))
        print(text, flush=True)
        if not send(text):
            return found
    save(found, state, now)
    return found


def watch(end):
    state, errors, found = NHPLUG / 'vibe-longterm' / 'watch-state.json', 0, {}
    while (now := datetime.now(KST)).strftime('%H:%M') < end:
        try:
            found, errors = step(discord, state, now), 0
        except Exception as exc:  # noqa: BLE001 - a broken record must not end the watch silently
            errors += 1
            print(f'{now:%H:%M} {type(exc).__name__}: {str(exc)[:200]}', flush=True)
            if errors == 3:
                discord(f'🔎 장기투자 감시 · {now:%m-%d %H:%M}\n⚠️ 감시기가 3회 연속 실패({type(exc).__name__}), 계속 다시 시도')
        time.sleep(60)
    discord(mask(f'🔎 장기투자 감시 종료 · {now:%m-%d %H:%M} · 남은 문제 {len(found)}건' + ''.join(f'\n- {v}' for v in found.values())))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--watch', action='store_true', help='the service: every minute until --until, changes to Discord')
    parser.add_argument('--until-change', action='store_true', help='check every minute and exit on the first new or cleared finding')
    parser.add_argument('--state', help='JSON file of the findings already reported today')
    parser.add_argument('--until', default='16:00', help='HH:MM the watch ends')
    args = parser.parse_args()
    if args.until_change and not args.state:
        parser.error('--until-change needs --state')
    import vibe_longterm as L
    now = datetime.now(KST)
    if args.watch:
        if L.trading_day(now.date()) and now.strftime('%H:%M') < args.until:
            watch(args.until)
        return
    errors = 0
    while True:
        now = datetime.now(KST)
        if not L.trading_day(now.date()):
            print('휴장일'); return
        try:
            found, errors = findings(collect(now=now), now), 0
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:  # a file mid-write; the watcher itself must not die quietly
            errors += 1
            if errors >= 3 or not args.until_change:
                print(f'감시기 오류 {type(exc).__name__}: {exc}'); raise SystemExit(1)
            time.sleep(20); continue
        shown = changes(found, args.state, now) if args.state else [('now', k, v) for k, v in found.items()]
        for status, key, text in shown:
            print(f'{now:%H:%M} [{status}] {key} — {text}', flush=True)
        if not args.until_change or shown:
            return
        if now.strftime('%H:%M') >= args.until:
            print(f'{now:%H:%M} 감시 종료 · 남은 문제 {len(found)}건'); return
        time.sleep(60)


if __name__ == '__main__':
    main()
