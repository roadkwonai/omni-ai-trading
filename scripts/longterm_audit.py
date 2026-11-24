"""장기투자 복기·감사 데이터 (user requests 2026-11-18, docs/audit/longterm-audit-2026-11-18.md 4): read-only facts for the
daily evening review and the weekly and monthly audits — what the engine observed each trading day, whether its inputs,
reasons and errors reached the decision log, why each order was placed (longterm_evidence), which reports and journal
views were written, NAV against the shadows, 적립식 and the live long-term accounts — plus the problems a plain check finds.
The morning strategy report (08:50) is the plan; this is the review: scheduled tasks longterm-review-daily (weekdays
16:45), longterm-audit-weekly (Fri 17:40) and longterm-audit-monthly (1st, 17:45) read --data and write
reviews/audit-<key>.md. They only propose; the user approves any change.

    PYTHONPATH=agent:scripts .venv/bin/python scripts/longterm_audit.py --data 2026-11-18     # that trading day
    ... --data 2026-W47 · --data 2026-11                                                     # a week · a month
    ... --blank <key> > reviews/audit-<key>.draft.md · --check <key> --file <draft> · --status
A draft name does not match the report pages pattern, so only a report moved to audit-<key>.md after --check passes is
published (report_pages kind longterm-audit, Discord LONGTERM).
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import longterm_template as T
import vibe_longterm as L

DAY_SECTIONS = ('요약', '오늘 관찰', '매매와 근거', '거장 관점', '거시 관점', '가상 계좌와 그림자', '기록·리포트 점검', '개선·보완 제안',
                '다음 거래일에 볼 것')
SECTIONS = ('요약', '관찰 범위', '판단 이력', '판단 근거', '리포트·일지', '동작 확인', '성과', '제안', '이전 제안 추적', '다음에 볼 것')
PLACEHOLDER = T.PLACEHOLDER
WEEK_DAYS = 5
KEYS = T.KINDS  # the same day · week · month keys as the strategy report
LABEL = {'day': '일일 복기·감사', 'week': '주간 감사', 'month': '월간 감사'}
# what a full trading day leaves in the decision log; 'trade' is a plan or an order row
EXPECTED = ('regime', 'macro', 'story', 'trade')


def kind_of(key):
    """'day' · 'week' · 'month' of a real date key (2026-13 or 2026-W60 is refused, not read as a period)."""
    try:
        kind = T.kind_of(key)
        {'day': date.fromisoformat, 'week': lambda k: datetime.strptime(f'{k}-1', '%G-W%V-%u'),
         'month': lambda k: date.fromisoformat(f'{k}-01')}[kind](key)
        return kind
    except ValueError:
        raise ValueError(f'알 수 없는 감사 키입니다: {key}') from None


def is_month(key):
    return kind_of(key) == 'month'


def sections(key):
    return DAY_SECTIONS if kind_of(key) == 'day' else SECTIONS


def title(key):
    return f'# 장기투자 {LABEL[kind_of(key)]} · {key}'


def path(root, key):
    return Path(root) / 'reviews' / f'audit-{key}.md'


MASTERS_SECTION = '거장 관점'


def masters_required(key):
    """A daily review from VIEWS_FROM on reads each of the seven masters by their own principle (as the strategy report)."""
    import longterm_report as R
    return kind_of(key) == 'day' and key >= R.VIEWS_FROM


def blank(key):
    import longterm_report as R
    body = lambda s: ('\n'.join(f"- **{m['name']}**(원칙: {m['principle']}) — 오늘 시장을 이 원칙으로 보면: {PLACEHOLDER} · "
                                f"이 거장이라면 할 일 vs 엔진·계좌가 한 일(같음/다름과 근거): {PLACEHOLDER}" for m in R.MASTER_VIEWS.values())
                      if s == MASTERS_SECTION and masters_required(key) else PLACEHOLDER)
    return '\n\n'.join([title(key)] + [f'## {i}. {s}\n\n{body(s)}' for i, s in enumerate(sections(key), 1)]) + '\n'


def check(text, key):
    """Korean problems with a written report ([] when it passes): title, every section in order, nothing left blank (a short
    '없음' is an answer, a bare '-' is not)."""
    problems = T.check_sections(text, title(key), sections(key), min_body=2)
    if masters_required(key):
        import longterm_report as R
        part = T.section_body(text, MASTERS_SECTION) or ''
        if missing := [m['name'] for m in R.MASTER_VIEWS.values() if f"**{m['name']}**" not in part]:
            problems.append(f'{MASTERS_SECTION}에 거장별 줄(**이름**)이 빠졌습니다: ' + ', '.join(missing))
    return problems


def window(key):
    """(last day, trading days) a key covers: that day, the ISO week's trading days, or the whole month."""
    kind = kind_of(key)
    if kind == 'month':
        year, month = map(int, key.split('-'))
        last = date(year, month, calendar.monthrange(year, month)[1])
        return last, sum(L.trading_day(date(year, month, d)) for d in range(1, last.day + 1))
    if kind == 'week':
        friday = datetime.strptime(f'{key}-5', '%G-W%V-%u').date()
        return friday, sum(L.trading_day(friday - timedelta(days=i)) for i in range(5))
    return date.fromisoformat(key), 1


def trading_days(end, n):
    out, d = [], end
    while len(out) < n and (end - d).days < 400:
        if L.trading_day(d):
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def _rows(root, days):
    db = Path(root) / 'longterm.sqlite3'
    if not db.exists() or not days:
        return [], []
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    marks = ','.join('?' * len(days))
    try:
        rows = [dict(r) for r in con.execute(f'SELECT * FROM decisions WHERE day IN ({marks}) ORDER BY rowid', days)]
        nav = [dict(r) for r in con.execute(f'SELECT * FROM nav WHERE day IN ({marks}) ORDER BY day', days)]
    finally:
        con.close()
    return rows, nav


def _json(text):
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def _started(root):
    """The engine's first logged day (None before it ever ran)."""
    db = Path(root) / 'longterm.sqlite3'
    if not db.exists():
        return None
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        return con.execute('SELECT min(day) FROM decisions').fetchone()[0]
    finally:
        con.close()


def _changes(span):
    """The long-term setting changes in the window (settings history kind vibe-longterm): who changed what, when."""
    from config_store import history_path
    db = history_path()
    if not span or not db.exists():
        return []
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        rows = con.execute("SELECT time, key, old, new, who FROM changes WHERE kind='vibe-longterm' AND substr(time,1,10) BETWEEN ? AND ? "
                           'ORDER BY time', (span[0], span[-1])).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [{'time': t, 'key': k, 'old': o, 'new': n, 'who': w} for t, k, o, n, w in rows]


def _journal_view(journal_dir, day):
    try:
        obj = json.loads((Path(journal_dir) / f'journal-{day}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return bool(((obj.get('views') or {}).get('longterm')))


def _live(day_set, live_log):
    out = Counter()
    try:
        lines = Path(live_log).read_text(encoding='utf-8').splitlines()
    except OSError:
        return {}
    for line in lines:
        e = _json(line) or {}
        if str(e.get('time', ''))[:10] in day_set:
            out[f"{e.get('account')}:{e.get('action')}"] += 1
    return dict(out)


SENT = ('ACKNOWLEDGED_NOT_FILLED', 'SENT_BEFORE_RESTART')  # a long-term live order that went out (longterm-live.jsonl)


def _fills(root, rows, span, live_log, now):
    """Orders against fills over the span (user 2026-11-18: checked in every review and audit): the virtual book's orders
    that did not fill all the way, the long-term live accounts' sent orders against their recorded fills (live_fills), a
    send recovered after a restart (SENT_BEFORE_RESTART: no second order may follow) and the 여운봉 step money still owed.
    Today's live orders are checked only after 15:30, when they have had the session to fill."""
    import longterm_live as LL
    nhplug = Path(root).parent
    virtual = [{'day': r['day'], 'key': o['key'], 'qty': o['qty'], 'filled': o['filled'], 'why': o.get('unfilled_why')}
               for r in rows if r['kind'] in ('order', 'dca') for o in (_json(r['orders']) or [])
               if isinstance(o, dict) and 'filled' in o and o['filled'] < o['qty']]
    sent, recovered, accounts = {}, [], set()
    try:
        lines = Path(live_log).read_text(encoding='utf-8').splitlines()
    except OSError:
        lines = []
    today, late = now.date().isoformat(), now.strftime('%H:%M') >= '15:30'
    for line in lines:
        e = _json(line) or {}
        day = str(e.get('time', ''))[:10]
        if day not in span or e.get('action') not in ('BUY', 'SELL') or e.get('execution') not in SENT:
            continue
        accounts.add(e.get('account'))
        if e['execution'] == 'SENT_BEFORE_RESTART':
            recovered.append({'day': day, 'account': e.get('account'), 'symbol': e.get('symbol')})
        if day < today or late:
            k = (day, e.get('account'), e.get('symbol'), e['action'])
            sent[k] = sent.get(k, 0) + (e.get('qty') or 0)
    live = []
    for (day, account, symbol, side), qty in sorted(sent.items()):
        filled = min(qty, LL.filled_on(nhplug, account, day, side).get(symbol, 0))
        if filled < qty:
            live.append({'day': day, 'account': account, 'symbol': symbol, 'side': side, 'qty': qty, 'filled': filled})
    owed = {'actual': (L.read_state(root, ('book:actual',)).get('book:actual') or {}).get('yeo_owed')}
    for account in sorted(a for a in accounts if a):
        state = LL.load_state(nhplug, account)
        owed[account] = (state.get('book_next') or state.get('book') or {}).get('yeo_owed')
    owed = {k: {a: round(v) for a, v in o.items()} for k, o in owed.items() if o}
    findings = ([f"{v['day']}: 가상 주문 미체결 — {L.asset_name(v['key'])} {v['filled']}/{v['qty']}주" + (f" ({v['why']})" if v['why'] else '') for v in virtual]
                + [f"{v['day']}: 실계좌 주문 미체결 — {v['account']} {v['symbol']} {'매수' if v['side'] == 'BUY' else '매도'} "
                   f"{v['filled']}/{v['qty']}주(체결 기록 기준)" for v in live]
                + [f"{v['day']}: 재시작 뒤 전송 복구 — {v['account']} {v['symbol']}(같은 주문이 다시 실행되지 않았는지 확인)" for v in recovered]
                + [f"여운봉 못 산 몫 대기 — {book}: " + ', '.join(f'{L.asset_name(a)} {v:,}원' for a, v in o.items()) for book, o in owed.items()])
    return {'virtual': virtual, 'live': live, 'recovered': recovered, 'owed': owed}, findings


def _dca_waits(root):
    """What waiting for a down day cost the virtual book's 적립식 (2026-11-24 복기 (B)1): for every buy after days it waited,
    the ask on the first waiting day against the price paid. Kept asks only (from 2026-11-25); a record, not a rule."""
    path = Path(root) / 'longterm.sqlite3'
    out, start = [], None
    if path.exists():
        from sqlite_ro import ro_connect
        with ro_connect(path) as db:
            found = db.execute("SELECT day, orders, indicators FROM decisions WHERE kind='dca' ORDER BY rowid").fetchall()
        for day, orders, seen in found:
            seen = _json(seen) or {}
            if not seen.get('asks'):
                continue
            if not seen.get('bought'):
                if start is None and (seen.get('waiting_after') or 0) >= 1_000:
                    start = (day, seen['asks'])
                continue
            for o in _json(orders) or [] if start else []:
                then = start[1].get(o.get('key'))
                if then and o.get('filled'):
                    out.append({'from': start[0], 'bought': day, 'key': o['key'], 'ask_then': then, 'paid': o['price'],
                                'diff_pct': round((o['price'] / then - 1) * 100, 2), 'krw': o['price'] * o['filled']})
            start = None
    krw = sum(r['krw'] for r in out)
    return {'rows': out[-20:], 'n': len(out), 'mean_diff_pct': round(sum(r['diff_pct'] * r['krw'] for r in out) / krw, 2) if krw else None,
            'waiting_since': start[0] if start else None}


def _evidence(root, span):
    """Why each order of the window was placed (longterm_evidence): counts and the one-sentence summaries."""
    import longterm_evidence as E
    rows = [r for r in E.rows(root, span[0], span[-1])] if span else []
    used = Counter(k for r in rows for k, p in (json.loads(r['bundle'] or '{}').get('parts') or {}).items() if p.get('used'))
    return {'count': dict(Counter(r['kind'] for r in rows)), 'unfilled': sum(1 for r in rows if r['number'] and not r['filled']),
            'reconstructed': sum(r['reconstructed'] for r in rows), 'used_parts': dict(used),
            'summaries': [f"{r['day']} {E.KIND_LABELS.get(r['kind'], r['kind'])}: {r['summary']}" for r in rows if r['kind'] != 'plan'][:40]}


def _ideas(root):
    db = Path(root) / 'ideas.sqlite3'
    if not db.exists():
        return {}
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        picks = con.execute('SELECT max(day), count(*) FROM picks').fetchone()
        theses = con.execute('SELECT count(*) FROM theses').fetchone()[0]
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return {'latest_screen': picks[0], 'picks': picks[1], 'theses': theses}


def collect(root=L.ROOT, end=None, days=WEEK_DAYS, journal_dir=None, live_log=None, now=None):
    """Facts for the audit of the `days` trading days ending at `end` (read-only)."""
    root = Path(root)
    end = end or datetime.now(L.KST).date()
    journal_dir = journal_dir or root.parent / 'shared-etf-paper' / 'reviews'
    live_log = live_log or root.parent / 'longterm-live.jsonl'
    span = trading_days(end, days)
    rows, nav = _rows(root, span)
    started = _started(root)
    coverage, findings = [], []
    today = datetime.now(L.KST).date().isoformat()
    for day in span:
        mine = [r for r in rows if r['day'] == day]
        kinds = Counter('trade' if r['kind'] in ('plan', 'order') else r['kind'] for r in mine)
        story = next((r for r in mine if r['kind'] == 'story'), None)
        source = (re.match(r'\[([^\]]+)\]', story['reason'] or '') or [None, None])[1] if story else None
        seen = _json(story['targets']) if story else None
        report = (root / 'reports' / f'strategy-{day}.md').exists()
        row = {'day': day, 'kinds': dict(kinds), 'missing': [k for k in EXPECTED if not kinds.get(k)],
               'story_source': source, 'story_sources': len((seen or {}).get('sources') or []),
               'errors': kinds.get('error', 0), 'missed': kinds.get('missed', 0), 'skips': kinds.get('skip', 0),
               'report': report, 'journal_view': _journal_view(journal_dir, day), 'nav': any(n['day'] == day for n in nav)}
        coverage.append(row)
        if day > today or not started or day < started:  # nothing is expected before the engine's first day
            row['before_start'] = not started or day < started
            continue
        if row['missing']:
            findings.append(f"{day}: 판단 기록 빠짐 — {', '.join(row['missing'])}")
        if source and source != 'llm':
            findings.append(f"{day}: 테마 스토리를 새로 판단하지 못함({source}) — {'기본값(유지)' if source.startswith('default') else '이전 판단 재사용'}")
        done = {'decide': ('regime',), 'trade': ('plan', 'order'), 'dca': ('dca',)}  # a 'missed' step whose own record that day exists did run
        ran = {k for k, kinds in done.items() if any(x['kind'] in kinds for x in mine)}
        for r in mine:
            if r['kind'] == 'missed' and (r['reason'] or '').split(':')[0] in ran:
                continue
            if r['kind'] in ('error', 'missed', 'skip'):
                findings.append(f"{day}: {L.DECISION_KINDS.get(r['kind'], r['kind'])} — {r['reason']}")
        if not report:
            findings.append(f'{day}: 일일 전략 리포트 없음')
        if row['journal_view'] is False:
            findings.append(f'{day}: 매매일지에 장기투자 관점(views.longterm) 없음')
        if not row['nav'] and day < today:
            findings.append(f'{day}: 장 마감 순자산(nav) 기록 없음')
    books = {}
    for book in sorted({n['book'] for n in nav}):
        values = [(n['day'], n['value']) for n in nav if n['book'] == book]
        books[book] = {'first': values[0], 'last': values[-1], 'change': values[-1][1] - values[0][1]}
    trades = [r for r in rows if r['kind'] in ('plan', 'order')]
    notes = Counter(n[0] for r in trades for n in ((_json(r['targets']) or {}).get('notes') or []) if isinstance(n, list) and n)
    regimes = [(r['day'], r['regime'], r['raw_regime']) for r in rows if r['kind'] == 'regime']
    dca = [{'day': r['day'], 'orders': len(_json(r['orders']) or []), 'reason': r['reason'], 'seen': _json(r['indicators'])}
           for r in rows if r['kind'] == 'dca']
    fills, found = _fills(root, rows, span, live_log, now or datetime.now(L.KST))
    findings += found
    settings = L.load_settings(root)
    reports = sorted(p.stem.removeprefix('strategy-') for p in (root / 'reports').glob('strategy-*.md')) if (root / 'reports').exists() else []
    audits = sorted(status(root)['keys'])
    return {
        'days': span, 'started': started, 'coverage': coverage, 'findings': findings,
        'settings_changes': _changes(span),
        'regimes': regimes, 'regime_changes': sum(1 for a, b in zip(regimes, regimes[1:]) if a[1] != b[1]),
        'trades': {'rows': len(trades), 'executed': sum(r['kind'] == 'order' for r in trades),
                   'orders': sum(len(_json(r['orders']) or []) for r in trades), 'notes_by_module': dict(notes),
                   'reasons': [r['reason'] for r in trades][-10:]},
        'dca': dca, 'nav': books, 'live': _live(set(span), live_log), 'ideas': _ideas(root), 'evidence': _evidence(root, span),
        'settings': {'mode': settings['mode'], 'execute': settings['execute'], 'enabled': settings['enabled'],
                     'modules': [m for m, on in settings['modules'].items() if on],
                     'dca_on': {k: v['on'] for k, v in settings['dca'].items()}},
        'reports': [k for k in reports if k <= span[-1]][-12:] if span else reports[-12:], 'previous_audits': audits[-6:],
        'masters': _masters(root, span), 'fills': fills, 'dca_waits': _dca_waits(root),
    }


def _masters(root, span):
    """거장 관점 facts: each master's principle and portfolio, the morning reports' master_views (the plan to review) and
    where each 여운봉 저수지 매매법 book stands."""
    import longterm_report as R
    morning = {}
    for day in span:
        try:
            views = json.loads((Path(root) / 'reports' / f'strategy-{day}.json').read_text(encoding='utf-8')).get('master_views')
        except (OSError, ValueError, AttributeError):
            views = None
        if views:
            morning[day] = views
    state = L.read_state(root, ('plan', 'book:actual'))
    return {'principles': {k: {f: m[f] for f in ('name', 'principle', 'portfolio', 'modules')} for k, m in R.MASTER_VIEWS.items()},
            'morning': morning, 'yeo_accounts': R.yeo_accounts(root, state.get('plan') or {}, state)}


def status(root=L.ROOT):
    """The audit reports written so far and the newest one's check: for the dashboard."""
    folder = Path(root) / 'reviews'
    keys = sorted((k for p in folder.glob('audit-*.md') if any(re.fullmatch(x, k := p.stem.removeprefix('audit-')) for x in KEYS.values())),
                  reverse=True) if folder.exists() else []  # drafts (audit-<key>.draft.md) are not reports
    return {'keys': keys, 'latest': keys[0] if keys else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--data', metavar='KEY', help='YYYY-MM-DD (that day), YYYY-Www (the ISO week) or YYYY-MM (the month)')
    group.add_argument('--blank', metavar='KEY')
    group.add_argument('--check', metavar='KEY')
    group.add_argument('--status', action='store_true')
    parser.add_argument('--file', help='with --check: a draft to check instead of reviews/audit-KEY.md (publish only a passing report)')
    parser.add_argument('--publish', action='store_true', help='with --check --file: when it passes, write it as reviews/audit-KEY.md (0600)')
    args = parser.parse_args()
    if args.data:
        end, n = window(args.data)
        print(json.dumps({'key': args.data, 'kind': LABEL[kind_of(args.data)], **collect(L.ROOT, end, n)}, ensure_ascii=False, default=str, indent=1))
    elif args.blank:
        print(blank(args.blank), end='')
    elif args.check:
        file = Path(args.file) if args.file else path(L.ROOT, args.check)
        problems = check(file.read_text(encoding='utf-8'), args.check) if file.exists() else [f'{file.name} 없음']
        published = None
        if args.publish and args.file and not problems:  # the scheduled task needs no mv/chmod (both waited for approval)
            target = path(L.ROOT, args.check)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file.read_text(encoding='utf-8'), encoding='utf-8')
            target.chmod(0o600)
            published = str(target)
        print(json.dumps({'ok': not problems, 'problems': problems, **({'published': published} if args.publish else {})}, ensure_ascii=False))
    else:
        print(json.dumps(status(), ensure_ascii=False))


if __name__ == '__main__':
    main()
