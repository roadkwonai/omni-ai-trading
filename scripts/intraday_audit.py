"""Read-only facts for the hourly intraday audit (Codex automation "장중 매매·차단 감사", skill intraday-audit).

Collects what every audit round re-reads by hand: launchd state, ledgers (fills, unfinished intents, cycle gaps, entry
blocks), premarket inputs actually recorded in model_inputs, live mirror mode and switches (the intraday vibe book, its
quote snapshots, event pipeline and replay retired 2026-11-17 and are no longer read). It also
turns hard facts into anomaly keys and marks each new / ongoing / resolved against the keys already written in today's
audit file, so the same problem is not reported twice, and folds them into the cross-day incident history
(audit_incidents: new / open / resolved / recurred, the only file it writes). Facts only: it never orders, edits
settings or touches services.

    python scripts/intraday_audit.py                 # JSON for now
    python scripts/intraday_audit.py --since 10:20   # window counts since the previous round
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import api_load
import os_services
from src.config.paths import get_runtime_root  # VIBE_TRADING_HOME, else ~/.vibe-trading (one rule for every tool)
from sqlite_ro import day_rows  # a line is the day's by its own time, not by mentioning the day

KST = ZoneInfo('Asia/Seoul')
NHPLUG = get_runtime_root() / 'nhplug'
SERVICES = ('shared-etf-paper', 'shared-etf-learning', 'shared-etf-regime', 'trading-day-start',
            'market-movers', 'premarket', 'live-follow-1', 'vibe-longterm', 'longterm-live-2', 'discord-notify')  # old vibe services and live-follow-2 retired 2026-11-17
# Started at 09:00 and expected to stay up through the session (trading-day-start/premarket exit by design).
SESSION_SERVICES = ('shared-etf-paper', 'shared-etf-learning', 'shared-etf-regime', 'market-movers', 'live-follow-1', 'vibe-longterm')
SERVICE_UNTIL = {'shared-etf-regime': '15:00', 'market-movers': '15:05'}  # etf_regime_observer stops itself at 15:00 (11/18 false service.down)  # longterm: 08:40~15:40 (longterm-live-2 runs 09:35~14:40 only)
LEDGERS = ('shared-etf-paper',)  # the intraday vibe book is frozen and jev-virtual retired (2026-11-17)
CYCLE_GAP_MIN = 3        # a runner cycle is ~1 minute; a longer silence is worth a look
REPEAT_BLOCK_ROUNDS = 3  # the same pick refused this many minutes in a row (user 2026-11-19: a per-minute stall is fatal)
NH_SAME_QUERY_HITS = 10  # NH same-query refusals (IGW42903) per caller in the window; 11-19 ran ~40 an hour, hidden by retries
NH_APP_LIMIT_HITS = 20  # NH per-app refusals (IGW42902, 5 calls a second for every service) per caller; 11-19 movers ~26 an hour
RESTART_GRACE_MIN = 5    # restart_runner appends restarts.jsonl only after the new runner's first cycle
FIGURES_MAX_AGE_MIN = 14  # latest_mover_rows' 12 minutes + the HH:MM truncation and Jev's answer time
REGRESSION_SHARE = 0.2   # source-fed inputs: a few gaps are an outage, a fifth of the calls is a regression
SOURCE_LAG_MIN = {'index_intraday': 20, 'program_intraday': 20, 'investor_rankings': 20, 'theme_leaders': 20,  # INTRADAY_MAX_LAG
                  'global_intraday': 30,  # market_context.GLOBAL_INTRADAY_MAX_LAG: Yahoo futures run ~10-20 minutes behind
                  'investor_rankings:기관': 150}  # Toss posts institutions in batches (10:00, 11:05, 13:20 on 11/18)
SHADOW_SILENT_MIN = 20  # the movers loop records shadow candidates every 5 minutes
BALANCE_MAX_AGE_MIN = {'jev': 15}  # the mirror records every 5 minutes; the long-term executor idles after its plan
# A finding key always has a dot (service.down:x); [check-done:id] and markdown [1] are not findings.
KEY_PATTERN = re.compile(r'\[((?:resolved:)?[a-z0-9_-]+\.[a-z0-9_.:-]+)\]')
CHECKS = Path(__file__).resolve().parent.parent / '.agents/skills/intraday-audit/references/checks.md'


def _time(value):
    try:
        stamp = datetime.fromisoformat(str(value))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=KST)
    except ValueError:
        return None


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _query(path, sql, args=()):
    from sqlite_ro import ro_connect
    import sqlite3
    try:
        with ro_connect(path, timeout=0.5) as db:
            return db.execute(sql, args).fetchall()
    except sqlite3.Error:
        return None


def services(run=subprocess.run):
    out = {}
    for name in SERVICES:
        try:
            row = os_services.state(name, run=run)
        except os_services.Unknown:
            out[name] = {'registered': False, 'unknown': True}
            continue
        out[name] = {'registered': False} if not row['registered'] else \
            {'registered': True, 'pid': row['pid'], 'running': row['running'], 'last_exit': row['last_exit'], **({'started': row['started']} if row['running'] else {})}
    return out


def repeat_blocks(events):
    """Runs of one symbol refused for one reason at the order gate in consecutive minutes (gaps up to 150 s), at least
    REPEAT_BLOCK_ROUNDS long: [{symbol, reason, from, to, count}]."""
    runs, out = {}, []

    def close(key, run):
        if run['count'] >= REPEAT_BLOCK_ROUNDS:
            out.append({'symbol': key[0], 'reason': key[1], 'from': run['first'].strftime('%H:%M'),
                        'to': run['last'].strftime('%H:%M'), 'count': run['count']})
    for e in sorted(events, key=lambda e: str(e.get('time'))):
        at, key = _time(e.get('time')), (e.get('symbol') or e.get('choice'), e.get('reason'))
        if e.get('event') != 'v2_execution_gate' or e.get('permitted') is not False or at is None or None in key:
            continue
        run = runs.get(key)
        if run and (at - run['last']).total_seconds() <= 150:
            run['last'], run['count'] = at, run['count'] + 1
        else:
            if run:
                close(key, run)
            runs[key] = {'first': at, 'last': at, 'count': 1}
    for key, run in runs.items():
        close(key, run)
    return out


def zero_totals(nhplug, now, since):
    """{account key: count} of live balances refused since `since` (else the last hour) as impossible (live_fills.record_balance): 11-23 the
    totals of a paged balance were read from its first page, where NH gives 0."""
    out, start = {}, since or now - timedelta(hours=1)
    try:
        lines = (Path(nhplug) / 'balance-rejected.jsonl').read_text(encoding='utf-8').splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
            if _time(row['time']) >= start:
                out[row['account']] = out.get(row['account'], 0) + 1
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return out


def nh_limits(nhplug, now, since):
    """{caller: {'IGW42903': same-query refusals, 'IGW42902': per-app refusals}} since `since` (else the last hour) from
    api-calls.sqlite3."""
    start = (since or now - timedelta(hours=1)).isoformat(timespec='seconds')
    rows = _query(Path(nhplug) / 'api-calls.sqlite3', "SELECT caller, CASE WHEN detail LIKE '%IGW42903%' THEN 'IGW42903' ELSE 'IGW42902' END, "
                  "COUNT(*) FROM failures WHERE time >= ? AND time <= ? AND (detail LIKE '%IGW42903%' OR detail LIKE '%IGW42902%') "
                  "GROUP BY 1, 2", (start, now.isoformat(timespec='seconds')))
    if rows is None:
        return None
    out = {}
    for caller, code, n in rows:
        out.setdefault(caller, {})[code] = n
    return out


REPO = Path(__file__).resolve().parents[1]
CLI_READS = ('status trades', 'live status', 'status alerts', 'status balance', 'jev positions', 'jev blocks', 'account list', 'settings export', 'ops services')
CLI_STALE = 5  # the CLI's own word for old or missing data: an answer, not a failure


def _omni(args):
    """(exit code, data) of one local read of the omni CLI; (-1, None) when it gives no JSON or no answer in time."""
    import sys
    try:
        done = subprocess.run([sys.executable, '-m', 'cli_anything.omni_trading', '--json', *args], capture_output=True, text=True, encoding='utf-8',
                              timeout=60, cwd=REPO, env={**os.environ, 'PYTHONPATH': str(REPO / 'agent-harness')})
        return done.returncode, json.loads(done.stdout).get('data')
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1, None


def _cli_reference():
    import sys
    try:
        return subprocess.run([sys.executable, str(REPO / 'agent-harness/build_reference.py'), '--check'], capture_output=True, timeout=60,
                              cwd=REPO).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def cli_health(nhplug, now, omni=_omni, reference=_cli_reference):
    """{finding key: detail}: local reads of the omni CLI that fail, and answers that disagree with the records they are
    read from (11-25: the CLI said new buys were allowed while live-1's daily loss halt was on). It makes no plan and
    asks no broker. A record that changes while it is read is compared with both its states."""
    day, nhplug, found = now.date().isoformat(), Path(nhplug), {}

    def fills():
        rows = _query(nhplug / 'shared-etf-paper' / 'orders.sqlite3',
                      'SELECT side, symbol, SUM(quantity) FROM fills WHERE time LIKE ? GROUP BY side, symbol', (day + '%',))
        return None if rows is None else {(side, symbol): qty for side, symbol, qty in rows}

    def halts():
        rows = _query(nhplug / 'live-orders.sqlite3', 'SELECT COUNT(*) FROM live_halts WHERE day=?', (day.replace('-', ''),))
        return rows[0][0] if rows else None

    before, answers = (fills(), halts()), {}
    for name in CLI_READS:
        code, answers[name] = omni(name.split())
        if code not in (0, CLI_STALE):
            found['cli.failed:' + name.replace(' ', '-')] = f'omni {name} 종료 {code}'
        if name == 'live status':
            after = (fills(), halts())
    told = Counter()
    for row in (answers['status trades'] or {}).get('trades') or ():
        if row['account'].startswith('모의계좌'):
            told[row['side'], row['symbol']] += row['qty']
    if 'cli.failed:status-trades' not in found and None not in (before[0], after[0]) and dict(told) not in (before[0], after[0]):
        found['cli.mismatch:paper-fills'] = 'omni status trades의 모의 체결이 주문 원장과 다름'
    said = [a.get('loss_halt_today') for a in ((answers['live status'] or {}).get('live_accounts') or {}).values()]
    if 'cli.failed:live-status' not in found and '확인 불가' not in said and None not in (before[1], after[1]) and said.count(True) not in (before[1], after[1]):
        found['cli.mismatch:loss-halt'] = f'omni live status의 하루 손실 정지 {said.count(True)}개, 가드 기록 {after[1]}개'
    if not reference():
        found['cli.reference_stale'] = '참조 문서(docs/omni-cli-reference.md)가 코드와 다름: agent-harness/build_reference.py'
    return found


def ledger(root, day, since):
    root = Path(root)
    fills = _query(root / 'orders.sqlite3', 'SELECT side, COUNT(*), SUM(quantity) FROM fills WHERE time LIKE ? GROUP BY side',
                   (f'{day}%',))
    open_intents = _query(root / 'orders.sqlite3', "SELECT COUNT(*) FROM intents WHERE status != 'terminal'")
    events, bad = day_rows(root / 'events.jsonl', day)
    out = {'fills': {side: {'count': n, 'qty': q} for side, n, q in fills} if fills is not None else None,
           'unfinished_intents': open_intents[0][0] if open_intents else None, 'bad_event_lines': bad,
           'ingest_error': _json(root / 'ledger-ingest-error.json')}  # journal_event leaves it while ingest fails
    if events is None:
        return {**out, 'events': None}
    window = [e for e in events if since is None or (_time(e.get('time')) or since) >= since]
    strength_blocks = {}
    for event in window:
        reasons = event.get('strength_reasons')
        if event.get('event') != 'v2_entry_gate' or event.get('permitted') is not False or not isinstance(reasons, dict):
            continue
        for symbol, reason in reasons.items():
            if isinstance(reason, str) and reason:
                strength_blocks.setdefault(symbol, Counter())[reason] += 1
    newest = max(filter(None, (_time(e.get('time')) for e in events)), default=None)
    restarts, _ = day_rows(root / 'restarts.jsonl', day)
    recorded = {(r.get('stopped') or {}).get('time') for r in restarts or () if isinstance(r, dict)}
    # a session stop the runner came back from, with no restart_runner record of why (11/18 12:20)
    out['restarts_unrecorded'] = [e['time'] for e in window if e.get('event') == 'stopped' and _time(e.get('time'))
                                  and '09:00' <= _time(e['time']).strftime('%H:%M') < '15:20'
                                  and _time(e['time']) < newest and e['time'] not in recorded]
    # Every runner prints one {"status", "time"} line per cycle to its launchd log; events.jsonl does not (vibe).
    beats, _ = day_rows(root / f'com.vibetrading.{root.name}.log', day)
    beats = [(_time(b.get('time')), b.get('status')) for b in beats or ()
             if isinstance(b, dict) and _time(b.get('time')) and _time(b['time']).strftime('%H:%M') >= '09:00']
    starts = [at for at, _ in beats]
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(starts, starts[1:])]
    out.update(
        events=dict(Counter(e.get('event') for e in window).most_common()),
        last_event=max((e.get('time') for e in events if e.get('time')), default=None),
        cycles=len(starts), first_cycle=starts[0].isoformat() if starts else None,
        last_cycle=starts[-1].isoformat() if starts else None,
        cycle_status=dict(Counter(status for at, status in beats if since is None or at >= since).most_common()),
        max_cycle_gap_min=round(max(gaps), 1) if gaps else None,
        long_gaps=[{'after': a.strftime('%H:%M:%S'), 'minutes': round((b - a).total_seconds() / 60, 1)}
                   for a, b in zip(starts, starts[1:]) if (b - a).total_seconds() / 60 > CYCLE_GAP_MIN],
        entry_blocks=dict(Counter(e.get('reason') for e in window
                                  if e.get('event') in ('v2_entry_gate', 'v2_execution_gate') and e.get('permitted') is False
                                  ).most_common()),
        strength_blocks={symbol: dict(reasons) for symbol, reasons in strength_blocks.items()},
        repeat_blocks=repeat_blocks(window),
        pick_cooldowns=sum(e.get('event') == 'pick_cooldown' for e in window),
        errors=dict(Counter(e.get('event') for e in window if 'error' in str(e.get('event'))).most_common()),
        jev_busy=sum('JevRequestBusy' in json.dumps(e, ensure_ascii=False) for e in window),
        overnight_hold=[{k: e.get(k) for k in ('time', 'symbol', 'permitted', 'reason')}
                        for e in events if e.get('event') == 'overnight_hold'],
        oversold_atr=[{k: e.get(k) for k in ('time', 'symbol', 'status', 'need_pct', 'drop_pct', 'retry_seconds')}
                      for e in window if e.get('event') == 'oversold_atr_status'],
        oversold={name: {'count': sum(e.get('event') == name for e in window),
                         'premarket_audit': dict(Counter((e.get('premarket_audit') or {}).get('status')
                                                         for e in window if e.get('event') == name)),
                         'fallback_reason': dict(Counter(e.get('fallback_reason') for e in window
                                                         if e.get('event') == name and e.get('fallback_reason')))}
                  for name in ('oversold_signal', 'oversold_v2_signal', 'oversold_research_failure')
                  if any(e.get('event') == name for e in window)})
    return out


def premarket(nhplug, day):
    shared = Path(nhplug) / 'shared-etf-paper'
    brief = _json(shared / 'premarket-brief.json') or {}
    detail = shared / 'reviews' / f'premarket-{day}.md'
    rows = _query(shared / 'market-research.sqlite3',
                  'SELECT engine, question, inputs FROM model_inputs WHERE time >= ?', (day,))
    received = Counter()
    for engine, question, inputs in rows or ():
        try:
            data = json.loads(inputs)
            context = data.get('state' if engine == 'jev' else 'context') or {}
            has = isinstance(context.get('premarket_context'), dict)
        except (ValueError, AttributeError):
            has = None
        received[(f'{engine}.{question}', {True: 'with', False: 'without', None: 'unreadable'}[has])] += 1
    by_question = {}
    for (name, state), n in received.items():
        by_question.setdefault(name, {})[state] = n
    return {'brief_time': brief.get('time'), 'brief_trading_day': brief.get('trading_day'),
            'brief_sections': {k: (v or {}).get('status') for k, v in (brief.get('sections') or {}).items()},
            'detail_report': detail.name if detail.exists() else None,
            'detail_written': datetime.fromtimestamp(detail.stat().st_mtime, KST).isoformat() if detail.exists() else None,
            'model_inputs': by_question if rows is not None else None}


def live(nhplug, day, since=None):
    rows, _ = day_rows(Path(nhplug) / 'live-follow.jsonl', day)
    last, replacement_checks = {}, []
    for row in rows or ():
        last[row.get('account')] = {k: row.get(k) for k in (
            'time', 'mode', 'action', 'reason', 'execution', 'pending', 'lag_alert',
            'symbol', 'qty', 'price', 'held', 'target', 'cancelled_sells',
            'same_cycle_replacement', 'replacement_recheck_seconds')}
        if row.get('replacement_recheck_seconds') is not None and (since is None or (_time(row.get('time')) or since) >= since):
            replacement_checks.append({'account': row.get('account'), **last[row.get('account')]})
    try:
        from accounts import load
        records = {key: rec for key, rec in load(Path(nhplug) / 'accounts.json').items() if rec.get('kind') == 'live'}
    except Exception as exc:  # report, never guess a switch state
        return {'last': last, 'replacement_checks': replacement_checks, 'live_orders': {'error': type(exc).__name__},
                'accounts': None}
    import live_fills
    accounts = {}
    for key, rec in records.items():
        snaps = live_fills.balances(nhplug, key, day)
        fills = [r for r in live_fills.rows(nhplug, key) if r['time'].startswith(day)]
        accounts[key] = {'engine': rec.get('engine'), 'balance_rows': len(snaps),
                         'balance_last': snaps[-1].get('time') if snaps else None, 'fills_today': len(fills),
                         'fill_time_unknown': sum('T00:00:00' in r['time'] for r in fills)}  # rows() found no order time
    return {'last': last, 'replacement_checks': replacement_checks,
            'live_orders': {key: bool(rec.get('live_orders')) for key, rec in records.items()},
            'accounts': accounts}


def sources(root):
    """Minutes each intraday section of market-context.json trails the payload's own time (market_context.intraday_view
    leaves a part out past its limit, so the engines silently lose it). A stamp from before today's open counts from
    09:00: Toss's institution ranking keeps yesterday's 20:20 until its first batch."""
    payload = _json(Path(root) / 'market-context.json') or {}
    at = _time(payload.get('time'))
    if at is None:
        return None
    at = at.astimezone(KST)
    stamps = {section: {market: (item or {}).get('as_of') for market, item in ((payload.get(section) or {}).get('markets') or {}).items()}
              for section in ('index_intraday', 'program_intraday', 'global_intraday')}
    stamps['theme_leaders'] = {t.get('theme'): t.get('as_of') for t in (payload.get('theme_leaders') or {}).get('themes') or []
                               if isinstance(t, dict) and t.get('theme')}
    investors = (payload.get('investor_rankings') or {}).get('investors') or {}
    stamps['investor_rankings'] = {who: (investors.get(who) or {}).get('ranked_at') for who in ('외국인', '기관') if who in investors}
    lag = {}
    for section, parts in stamps.items():
        for part, stamp in parts.items():
            hhmm = isinstance(stamp, str) and re.fullmatch(r'(\d\d):(\d\d)', stamp)
            when = at.replace(hour=int(hhmm[1]), minute=int(hhmm[2]), second=0, microsecond=0) if hhmm else _time(stamp)
            if when is not None:
                start = max(when.astimezone(KST), at.replace(hour=9, minute=0, second=0, microsecond=0))
                lag.setdefault(section, {})[part] = round((at - start).total_seconds() / 60, 1)
    return {'time': payload.get('time'), 'lag_min': lag}


SUMMARY_FROM = '09:16'  # the first call with intraday_summary came at 09:12~09:14 on 11-20, 11-23 and 11-24
JEV_INPUT_KEYS = ('chart', 'recent_news_headlines', 'market_regime_themes_global', 'investor_daily_net_qty',
                  'premarket_context', 'own_track_record', 'trading_journal', 'intraday_summary', 'market_playbook')


def jev_inputs(root, day, since=None):
    """What Jev was actually given and why it says it answered (model_inputs + model_evidence, user 2026-11-18): input
    presence per key, news/market freshness, session-context coverage, and the evidence answers' distribution."""
    import statistics
    from collections import Counter
    rows = _query(Path(root) / 'market-research.sqlite3',
                  "SELECT i.time, i.question, i.inputs, e.evidence, i.response FROM model_inputs i LEFT JOIN model_evidence e "
                  "ON e.digest = i.digest WHERE i.engine='jev' AND i.time >= ? ORDER BY i.time",
                  ((since.isoformat() if since else day),))
    if rows is None:  # model_evidence absent before the first evidence answer: inputs alone
        rows = _query(Path(root) / 'market-research.sqlite3',
                      "SELECT time, question, inputs, NULL, response FROM model_inputs WHERE engine='jev' AND time >= ? ORDER BY time",
                      ((since.isoformat() if since else day),))
    if rows is None:
        return None
    from etf_news import JEV_NEWS_MAX_MINUTES, RECENT
    recent = RECENT.total_seconds() / 60
    # each question's first evidence answer: a call after it without one is a regression (watchlist never asks)
    evidence_from = dict(_query(Path(root) / 'market-research.sqlite3',
                                "SELECT question, MIN(time) FROM model_evidence WHERE engine='jev' GROUP BY question") or ())
    present, news_age, market_age, summary_bars, summary_calls = Counter(), [], [], [], 0
    from session_times import shifted
    evidence, questions, answers = {}, Counter(), {}
    fixes = Counter(dict.fromkeys(('news_old_calls', 'news_old_headlines', 'stock_news_old', 'session_market_calls',
                                   'market_intraday_missing', 'figures_calls', 'figures_stale', 'evidence_calls',
                                   'evidence_missing', 'global_intraday_missing'), 0))
    for stamp, question, raw, found, response in rows:
        questions[question] += 1
        try:  # the answers per question: e.g. the re-entry review's BUY share, holding SELL share
            answers.setdefault(question, Counter())[json.loads(response)['choice']] += 1
        except (ValueError, KeyError, TypeError):
            pass
        if question in evidence_from and stamp >= evidence_from[question]:
            fixes['evidence_calls'] += 1
            fixes['evidence_missing'] += not found
        try:
            data = json.loads(raw)
            state, at = data['state'], datetime.fromisoformat(stamp)
        except (ValueError, KeyError, TypeError):
            continue
        # the session summary needs several bars of today: not from the opening calls or the 30-minute watchlist pick
        due = question != 'watchlist' and at.astimezone(KST).strftime('%H:%M') >= shifted(SUMMARY_FROM, at.astimezone(KST).date())
        summary_calls += due
        present.update(k for k in JEV_INPUT_KEYS if state.get(k) and (due or k != 'intraday_summary'))
        checked_at = _time(state.get('news_checked_at'))
        news_at = checked_at if checked_at is not None and checked_at <= at else at
        ages = [(news_at - _time(n.get('published'))).total_seconds() / 60 for n in state.get('recent_news_headlines') or []
                if isinstance(n, dict) and _time(n.get('published'))]
        if ages:
            news_age.append(min(ages))
        market_news_limit = JEV_NEWS_MAX_MINUTES if state.get('news_max_age_minutes') == JEV_NEWS_MAX_MINUTES else recent
        old = sum(age > market_news_limit for age in ages)
        fixes['news_old_calls'] += bool(old)
        fixes['news_old_headlines'] += old
        fixes['stock_news_old'] += sum(  # recent_stock_headlines gives {title, age_min <= 90}; bare strings were the bug
            not (isinstance(h, dict) and type(h.get('age_min')) in (int, float) and h['age_min'] <= recent)
            for d in (state.get('watchlist_details') or {}).values() if isinstance(d, dict) for h in d.get('headlines') or [])
        view = state.get('market_regime_themes_global') or {}
        if view and '09:05' <= at.astimezone(KST).strftime('%H:%M') < '15:20':
            fixes['session_market_calls'] += 1
            fixes['market_intraday_missing'] += 'index_intraday' not in view
            fixes['global_intraday_missing'] += 'global_intraday' not in view  # phase 1 (user 2026-11-18): US futures, gold, oil
        stock = [text for code, text in (data.get('criteria') or {}).items()  # only the runner's pick carries figures
                 if question == 'candidate' and re.fullmatch(r'\d[0-9A-Z]{5}', code) and isinstance(text, str)]
        if stock:
            fixes['figures_calls'] += 1
            fixes['figures_stale'] += not any(_fresh_figures(text, at) for text in stock)
        if _time(f"{view.get('as_of')}:00+09:00"):
            market_age.append((at - _time(f"{view['as_of']}:00+09:00")).total_seconds() / 60)
        summary_bars += [s.get('bars', 0) for s in (state.get('intraday_summary') or {}).values() if isinstance(s, dict)]
        try:
            for key, answer in (json.loads(found) if found else {}).items():
                evidence.setdefault(key.removeprefix('why_'), Counter())[answer['choice']] += 1
        except (ValueError, KeyError, TypeError):
            pass
    calls = sum(questions.values())
    median = lambda v: round(statistics.median(v), 1) if v else None  # noqa: E731
    return {'calls': dict(questions), 'presence_pct': {k: round(present[k] / n * 100) for k in JEV_INPUT_KEYS
                                                           if (n := summary_calls if k == 'intraday_summary' else calls)},
            'news_newest_age_min': {'median': median(news_age), 'max': round(max(news_age), 1) if news_age else None},
            'market_age_min_max': round(max(market_age), 1) if market_age else None,
            'session_summary_bars_median': median(summary_bars),
            'evidence_calls': sum(1 for row in rows if row[3]), 'evidence': {k: dict(v) for k, v in evidence.items()},
            'answers': {q: dict(c) for q, c in answers.items()}, 'fix_checks': dict(fixes)}


def _fresh_figures(text, at):
    """A candidate line ends with '(HH:MM 기준)' from the latest ranking (with_latest_figures) no older than the limit."""
    found = re.search(r'\((\d\d):(\d\d) 기준\)$', text)
    if not found:
        return False
    at = at.astimezone(KST)
    lag = (at - at.replace(hour=int(found[1]), minute=int(found[2]), second=0, microsecond=0)).total_seconds() / 60
    return -1 <= lag <= FIGURES_MAX_AGE_MIN


LEARNING_ERROR_ROUNDS = 3
# The record databases (11-19 10:36 ledger corruption, src.tools.private_db); integrity_check takes ~0.02 s each
DATABASES = ('shared-etf-paper/trading-ledger.sqlite3', 'shared-etf-paper/orders.sqlite3', 'orders.sqlite3',
             'live-orders.sqlite3', 'loss.sqlite3', 'shared-etf-paper/market-research.sqlite3')


def database_health(nhplug):
    """{relative path: 'ok' or the first integrity_check problem} for the record databases that exist (read-only)."""
    import sqlite3
    out = {}
    for name in DATABASES:
        path = Path(nhplug) / name
        if not path.exists():
            continue
        try:
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as db:
                out[name] = db.execute('PRAGMA integrity_check').fetchone()[0]
        except sqlite3.Error as exc:
            out[name] = f'{type(exc).__name__}: {exc}'[:160]
    return out



def learning_rounds(log, day, tail=40):
    """The learning watcher's last minute rounds of `day` from its log: [(time, error_type or None)] (11-19: it ran but
    every round failed, which service.down cannot see)."""
    try:
        lines = Path(log).read_text(errors='replace', encoding='utf-8').splitlines()[-tail:]
    except OSError:
        return []
    rounds = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and str(row.get('time', '')).startswith(day) and ('imported' in row or 'error_type' in row):
            rounds.append((row['time'], row.get('error_type')))
    return rounds


def anomalies(facts, now, skipped=None):
    """Stable keys for hard facts only; interpretation stays with the auditor. `skipped` (a set) receives the prefixes
    or keys of the checks this run did not evaluate (outside the session, too few samples), so audit_incidents keeps
    their open incidents open instead of resolving them."""
    found = {}
    skipped = set() if skipped is None else skipped

    def ran(ok, *checks):
        if not ok:
            skipped.update(checks)
        return ok

    in_session = facts['trading_day'] and '09:05' <= now.strftime('%H:%M') < '15:20'
    for name in SESSION_SERVICES if ran(in_session, 'service.unregistered', 'service.down') else ():
        if not ran(now.strftime('%H:%M') < SERVICE_UNTIL.get(name, '15:20'), f'service.unregistered:{name}', f'service.down:{name}'):
            continue
        row = facts['services'].get(name, {})
        if not row.get('registered'):
            found[f'service.unregistered:{name}'] = '서비스 상태 확인 불가' if row.get('unknown') else '서비스 미등록'
        elif not row.get('running'):
            found[f'service.down:{name}'] = f"실행 중 아님(last_exit={row.get('last_exit')})"
    for name, row in facts['ledgers'].items():
        if row.get('unfinished_intents'):
            found[f'intents.unfinished:{name}'] = f"미종결 intent {row['unfinished_intents']}건"
        if row.get('events') is None:
            found[f'ledger.unreadable:{name}'] = 'events.jsonl 없음/읽기 실패'
        if row.get('ingest_error'):
            error = row['ingest_error']
            found[f'ledger.ingest_error:{name}'] = (f"{error.get('time')} {error.get('event')} 반영 실패({error.get('error_type')})"
                                                    if isinstance(error, dict) else 'ledger-ingest-error.json 있음')
        late = [t for t in row.get('restarts_unrecorded') or () if (now - _time(t)).total_seconds() / 60 > RESTART_GRACE_MIN]
        if late:
            found[f'runner.restart_unrecorded:{name}'] = ('restarts.jsonl 기록 없는 중단 뒤 재기동: '
                                                          + ', '.join(_time(t).strftime('%H:%M:%S') for t in late))
        last = _time(row.get('last_cycle'))
        if ran(in_session, 'cycle.silent') and (last is None or (now - last).total_seconds() / 60 > CYCLE_GAP_MIN):
            found[f'cycle.silent:{name}'] = f"마지막 회차 {row.get('last_cycle')}"
        stopped = (row.get('entry_blocks') or {}).get('jev_error', 0)
        if stopped >= REPEAT_BLOCK_ROUNDS:  # 11-24 13:12: entry judgments failed every cycle and no key named it
            found[f'entry.jev_stopped:{name}'] = f'Jev 진입 판단 실패 {stopped}회차(jev_error, 오류 문구는 events.jsonl detail)'
        for run in row.get('repeat_blocks') or ():
            found[f"entry.repeat_block:{run['symbol']}:{run['reason']}"] = f"{run['from']}~{run['to']} {run['count']}분 연속 같은 사유로 주문 단계 거절"
        for gap in row.get('long_gaps') or ():
            found[f"cycle.gap:{name}:{gap['after']}"] = f"{gap['after']} 뒤 {gap['minutes']}분 회차 공백"
    jev = facts.get('jev_inputs') or {}
    calls = sum((jev.get('calls') or {}).values())
    if ran(in_session and calls >= 10, 'jev.input_missing', 'jev.news_stale', 'jev.market_stale', 'jev.reentry_never_buy',
           'jev.data_insufficient'):
        for key, pct in (jev.get('presence_pct') or {}).items():
            if pct < 90 and (key != 'intraday_summary' or jev.get('evidence_calls')):  # session context ships with evidence
                found[f'jev.input_missing:{key}'] = f'Jev 요청 {calls}건 중 {key} 포함 {pct}%'
        if (jev['news_newest_age_min'].get('median') or 0) > 120:
            found['jev.news_stale'] = f"가장 최근 헤드라인 중앙값 {jev['news_newest_age_min']['median']}분 전"
        if (jev.get('market_age_min_max') or 0) > 15:
            found['jev.market_stale'] = f"시황 요약 최대 {jev['market_age_min_max']}분 지난 값"
        review = (jev.get('answers') or {}).get('reentry') or {}
        if ran(sum(review.values()) >= 10, 'jev.reentry_never_buy') and not review.get('BUY'):
            found['jev.reentry_never_buy'] = f'신중 재진입 재검토 {sum(review.values())}건 모두 WAIT(사실상 금지)'
        data = (jev.get('evidence') or {}).get('data') or {}
        if ran(sum(data.values()) >= 10, 'jev.data_insufficient') and data.get('ENOUGH', 0) / sum(data.values()) < 0.4:
            found['jev.data_insufficient'] = '자료 충분 응답 비율 40% 미만: ' + json.dumps(data, ensure_ascii=False)
    fixes = jev.get('fix_checks') or {}
    if ran(in_session, 'jev.news_old') and fixes.get('news_old_calls'):  # the fix filters every headline: one old one is a regression
        found['jev.news_old'] = f"Jev 호출 {fixes['news_old_calls']}건에 적용된 최신성 기준을 넘긴 시장 헤드라인 {fixes['news_old_headlines']}건"
    if ran(in_session, 'jev.stock_news_old') and fixes.get('stock_news_old'):
        found['jev.stock_news_old'] = f"시각 없음 또는 90분 넘은 종목 헤드라인 {fixes['stock_news_old']}건"
    for key, bad, total, label in (
            ('jev.market_intraday_missing', 'market_intraday_missing', 'session_market_calls', '장중 시황 구간(index_intraday) 없는 호출'),
            ('jev.global_intraday_missing', 'global_intraday_missing', 'session_market_calls', '장중 해외 선물·원자재(global_intraday) 없는 호출'),
            ('jev.candidate_figures_stale', 'figures_stale', 'figures_calls', '최신 순위 값(HH:MM 기준)이 없는 후보 호출'),
            ('jev.evidence_missing', 'evidence_missing', 'evidence_calls', '근거 답변 없는 호출')):
        if ran(in_session and fixes.get(total, 0) >= 5, key) and fixes.get(bad, 0) / fixes[total] > REGRESSION_SHARE:
            found[key] = f'{label} {fixes[bad]}/{fixes[total]}건'
    loops_due = in_session and now.strftime('%H:%M') >= '09:10'  # market-movers' first 09:00 round has written by now
    for name, result in (facts.get('databases') or {}).items():
        if result != 'ok':
            found[f'db.corrupt:{Path(name).name}'] = str(result)
    last = (facts.get('learning') or [])[-LEARNING_ERROR_ROUNDS:]
    if ran(in_session and len(last) == LEARNING_ERROR_ROUNDS, 'learning.error') and all(error for _, error in last):
        found['learning.error'] = f"학습 관찰기 최근 {len(last)}회차 모두 실패({last[-1][1]}, 마지막 {last[-1][0][11:19]})"
    shadow = facts.get('shadow') or {}  # issue_candidates round status (market_movers.shadow_round, phase 1 2026-11-18)
    shadow_at = _time(shadow.get('time'))
    if ran(loops_due and shadow_at, 'shadow.silent') and (now - shadow_at).total_seconds() / 60 > SHADOW_SILENT_MIN:
        found['shadow.silent'] = f"관찰 후보 기록 마지막 회차 {shadow.get('time')}"  # a stamp from an earlier day too
    if ran(in_session and shadow_at and shadow_at.astimezone(KST).date() == now.date(), 'shadow.source_error'):
        for name, error in (shadow.get('errors') or {}).items():
            found[f'shadow.source_error:{name}'] = str(error)
    source = facts.get('sources') or {}
    stamp = _time(source.get('time'))
    fresh = stamp and stamp.astimezone(KST).date() == now.date() and '09:05' <= stamp.astimezone(KST).strftime('%H:%M') < '15:20'
    if ran((in_session and fresh) or (loops_due and stamp), 'source.lagging') and not fresh:
        found['source.lagging:market_context'] = f"market-context.json 마지막 갱신 {source.get('time')}(오늘 장중 갱신 없음)"
    if in_session and fresh:
        for section, parts in (source.get('lag_min') or {}).items():
            late = []
            for part, lag in parts.items():
                limit = SOURCE_LAG_MIN.get(f'{section}:{part}', SOURCE_LAG_MIN[section])
                if lag > limit:
                    late.append(f'{part} {lag}분 지연(한도 {limit}분)')
            if late:
                found[f'source.lagging:{section}'] = ', '.join(late)
    for who, hits in (facts.get('nh_limits') or {}).items():
        if ran(in_session, 'nh.same_query_limit') and hits.get('IGW42903', 0) >= NH_SAME_QUERY_HITS:
            found[f'nh.same_query_limit:{who}'] = f"IGW42903 {hits['IGW42903']}건(같은 조회 1초 1번 제한, 재시도로 가려진 지연)"
        if ran(in_session, 'nh.app_rate_limit') and hits.get('IGW42902', 0) >= NH_APP_LIMIT_HITS:
            found[f'nh.app_rate_limit:{who}'] = f"IGW42902 {hits['IGW42902']}건(앱 키 초당 5회 제한, 같은 순간 다른 서비스 호출도 거절됨)"
    if ran(in_session, *(f'api.{kind}' for kind in api_load.ADVICE)):
        for row in facts.get('api_load') or ():
            if row['key'] != 'api.refused:nh':  # nh.same_query_limit and nh.app_rate_limit say it per caller
                found[row['key']] = f"{row['detail']} → {row['advice']}"
    if ran(in_session, 'live.zero_total'):
        for key, n in (facts.get('zero_totals') or {}).items():
            found[f'live.zero_total:{key}'] = f'잘못된 잔고 {n}건을 기록하지 않음(balance-rejected.jsonl), 같은 값을 읽는 가드가 새 매수를 막을 수 있음'
    if ran(facts.get('cli') is not None, 'cli.failed', 'cli.mismatch', 'cli.reference_stale'):
        found.update(facts['cli'])
    for key, on in facts['live']['live_orders'].items():
        if on is True:
            found[f'live.switch_on:{key}'] = '실계좌 실주문 스위치 켜짐'
    for key, row in facts['live'].get('last', {}).items():
        alert = row.get('lag_alert')
        if row.get('mode') != 'execute' or not alert or not alert.get('excess'):
            continue
        held, target = row.get('held') or {}, row.get('target') or {}
        remaining = {symbol: qty for symbol, qty in alert['excess'].items()
                     if symbol not in held or held[symbol] > target.get(symbol, 0)}
        if not remaining:
            continue
        excess = ', '.join(f'{symbol} {qty}주' for symbol, qty in sorted(remaining.items()))
        pending = f" · 미종결 {row['pending']}건" if type(row.get('pending')) is int else ''
        found[f'live.mirror_exit_lag:{key}'] = (
            f"{excess} 청산 지연 {alert['minutes']}분 · {row['time']} {row['action']}{pending}")
    for key, account in (facts['live'].get('accounts') or {}).items():
        if account['fill_time_unknown']:
            found[f'live.fill_time_unknown:{key}'] = f"오늘 체결 {account['fills_today']}건 중 {account['fill_time_unknown']}건 시각 없음(T00:00:00)"
        last, limit = _time(account['balance_last']), BALANCE_MAX_AGE_MIN.get(account['engine'])
        age = (now - last).total_seconds() / 60 if last else None
        if ran(in_session and now.strftime('%H:%M') >= '10:00', 'live.balance_missing') and (last is None or (limit and age > limit)):
            found[f'live.balance_missing:{key}'] = '오늘 잔고 기록 없음' if last is None else f'마지막 잔고 기록 {last:%H:%M} ({age:.0f}분 전)'
    return found


def classify(current, audit_file):
    """new / ongoing / resolved against keys already written in today's audit file."""
    try:
        previous = set(KEY_PATTERN.findall(Path(audit_file).read_text(encoding='utf-8')))
    except OSError:
        previous = set()
    resolved_marks = {k for k in previous if k.startswith('resolved:')}
    previous = {k for k in previous if not k.startswith('resolved:')} - {k.removeprefix('resolved:') for k in resolved_marks}
    return ([{'key': k, 'status': 'ongoing' if k in previous else 'new', 'detail': v} for k, v in sorted(current.items())]
            + [{'key': k, 'status': 'resolved'} for k in sorted(previous - set(current))])


def checks(reviews, path=CHECKS):
    """Dated one-off checks (### <id> headings) and the first audit file that marked each [check-done:<id>]."""
    try:
        ids = re.findall(r'^### ([a-z0-9-]+)', Path(path).read_text(encoding='utf-8'), re.M)
    except OSError:
        return None
    done = {}
    for audit in sorted(Path(reviews).glob('operations-audit-*.md')):
        text = audit.read_text(encoding='utf-8')
        for check in ids:
            if check not in done and f'[check-done:{check}]' in text:
                done[check] = audit.stem.removeprefix('operations-audit-')
    return {check: done.get(check) for check in ids}


def collect(nhplug=NHPLUG, now=None, since=None, run=subprocess.run, holidays=None, checks_file=CHECKS, incidents_db=None, cli=None):
    now = now or datetime.now(KST)
    day = now.date().isoformat()
    if holidays is None:
        try:
            from kr_swing_v5_daily_data import krx_holidays
            holidays, holiday_note = krx_holidays({now.year}), 'krx'
        except Exception as exc:
            holidays, holiday_note = frozenset(), f'holiday_table_unavailable:{type(exc).__name__}'
    else:
        holiday_note = 'given'
    from engine_roles import ledger_root, main_engine
    try:
        roles = {'main_engine': main_engine(nhplug), 'jev': ledger_root(nhplug, 'jev').name,
                 'vibe': ledger_root(nhplug, 'vibe').name}
    except Exception as exc:
        roles = {'error': type(exc).__name__}
    facts = {'time': now.isoformat(timespec='seconds'), 'day': day, 'since': since.isoformat() if since else None,
             'trading_day': now.weekday() < 5 and now.date() not in holidays, 'holiday_source': holiday_note,
             'roles': roles, 'services': services(run),
             'ledgers': {name: ledger(Path(nhplug) / name, day, since) for name in LEDGERS},
             'premarket': premarket(nhplug, day), 'live': live(nhplug, day, since),
             'shadow': _json(Path(nhplug) / 'shared-etf-paper' / 'shadow-status.json'),
             'databases': database_health(nhplug),
             'learning': learning_rounds(Path(nhplug) / 'shared-etf-paper' / 'com.vibetrading.shared-etf-learning.log', day),
             'jev_inputs': jev_inputs(Path(nhplug) / 'shared-etf-paper', day, since),
             'sources': sources(Path(nhplug) / 'shared-etf-paper'), 'nh_limits': nh_limits(nhplug, now, since),
             'zero_totals': zero_totals(nhplug, now, since), 'api_load': api_load.findings(api_load.collect(nhplug, now)),
             'cli': cli(nhplug, now) if cli else None}  # None = the omni CLI was not asked
    reviews = Path(nhplug) / 'shared-etf-paper' / 'reviews'
    facts['checks'] = checks(reviews, checks_file)
    skipped = set()
    found = anomalies(facts, now, skipped)
    import audit_incidents  # every class ever found, across days: new / open / resolved / recurred
    facts['incidents'] = audit_incidents.record(found, now, path=incidents_db or Path(nhplug) / 'audit-incidents.sqlite3',
                                                unchecked=skipped)
    if not facts['trading_day']:
        found['day.closed'] = '휴장일: 장중 거래 기대 없음'
    facts['findings'] = classify(found, reviews / f'operations-audit-{day}.md')
    return facts


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--since', help='HH:MM; window counts (events, blocks, oversold) start here')
    parser.add_argument('--incidents-db', help='incident history file (default NHPLUG/audit-incidents.sqlite3); a dry run points elsewhere')
    args = parser.parse_args()
    now = datetime.now(KST)
    since = None
    if args.since:
        hour, minute = map(int, args.since.split(':'))
        since = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    print(json.dumps(collect(now=now, since=since, incidents_db=args.incidents_db, cli=cli_health), ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
