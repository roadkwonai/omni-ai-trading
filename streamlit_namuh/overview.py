"""종합 현황: every account (shared paper, the secondary engine's virtual ledger, each live account) on one screen —
today's results, trades, holdings, judgments and executor activity, with charts. Read-only over local ledgers and logs
(no broker calls), so it opens fast; each account's own tab has the broker view."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import engine_compare as ec
import status_cards as sc
import live_labels as L

PALETTE = ['#a855f7', '#22c55e', '#f59e0b', '#06b6d4', '#ec4899', '#84cc16']


def accounts(runtime, records, now, names=None):
    """[{key, label, engine, kind, mode, rows, holdings, last}] for the paper accounts and every live account."""
    from engine_roles import ledger_root, main_engine
    runtime = Path(runtime)
    main = main_engine(runtime)
    out, engine_rows = [], {}
    for engine in ('jev',):  # the intraday vibe book is frozen since 2026-11-17 (엔진 비교 → 보관)
        root = ledger_root(runtime, engine)
        rows = ec.cached_journal(root, engine)
        engine_rows[engine] = (rows, root)
        state = ec.account_state(root, now, timedelta(minutes=10))
        if state:
            holdings = {c: {'qty': h.get('rsdl_qty') or 0, 'avg': h.get('phs_pr'), 'price': h.get('now_pr'),
                            'pnl': h.get('eal_pls_amt')} for c, h in (state.get('holdings') or {}).items() if h.get('rsdl_qty')}
        else:
            holdings = {c: {'qty': p['qty'], 'avg': p['avg'], 'price': None, 'pnl': None} for c, p in ec.positions(rows).items()}
        paper = engine == main
        label = (records.get('paper-shared') or {}).get('label', '모의계좌 (공유)') if paper else f'{ec.ENGINES[engine]} 가상'
        out.append({'key': engine, 'label': label, 'engine': ec.ENGINES[engine], 'kind': '모의(공유)' if paper else '가상 체결',
                    'mode': '실행', 'rows': rows, 'holdings': holdings,
                    'last': (f"{rows[-1]['time'][5:16].replace('T', ' ')} {'매수' if rows[-1]['side'] == 'BUY' else '매도'} "
                             f"{(names or {}).get(rows[-1]['symbol'], rows[-1]['symbol'])}") if rows else '거래 없음'})
    longterm = longterm_book(runtime / 'vibe-longterm')
    if longterm:
        out.append(longterm)
    follow = latest_follow(runtime)
    from accounts import longterm_accounts
    longterm = longterm_accounts(records)
    events = latest_longterm(runtime) if longterm else {}
    for key, record in records.items():
        if key in longterm:
            import longterm_account_view as LV
            import trading_journal
            mine = trading_journal._live_rows(key, '장기투자')  # its own broker fills; no mirror calculation
            event = events.get(key) or {}
            out.append({'key': key, 'label': record.get('label', key), 'engine': '장기투자', 'kind': '실계좌',
                        'mode': '실주문' if event.get('mode') == 'execute' else '관찰', 'rows': mine,
                        'holdings': {c: {'qty': p['qty'], 'avg': p['avg'], 'price': None, 'pnl': None} for c, p in ec.positions(mine).items()},
                        'last': f"{L.clock(event['time'], now.strftime('%Y-%m-%d'))} {LV.event_text(event)}" if event.get('time') else '기록 없음'})
            continue
        if record.get('kind') != 'live' or record.get('invalid') or record.get('engine') not in engine_rows:
            continue
        rows, root = engine_rows[record['engine']]
        mine = ec.account_rows(runtime, key, record, rows, root, now)
        event = follow.get(key) or {}
        positions = ec.positions(mine)
        held = event.get('held') or {}
        holdings = {c: {'qty': held.get(c, p['qty']), 'avg': p['avg'], 'price': None, 'pnl': None} for c, p in positions.items()}
        mode = '실주문' if event.get('mode') == 'execute' else '관찰'
        last = (f"{L.clock(event['time'], now.strftime('%Y-%m-%d'))} {L.ACTIONS.get(event.get('action'), event.get('action', ''))} · {L.reason(event.get('reason'))}"
                if event.get('time') else '기록 없음')
        out.append({'key': key, 'label': record.get('label', key), 'engine': ec.ENGINES[record['engine']], 'kind': '실계좌',
                    'mode': mode, 'rows': mine, 'holdings': holdings, 'last': last})
    return out


latest_follow = ec.latest_follow  # shared with the live tab and the settings cards


def longterm_book(root):
    """The long-term engine's virtual account (1억) as an account row, or None before its first decision."""
    root = Path(root)
    if not (root / 'longterm.sqlite3').exists():
        return None
    import vibe_longterm as LT
    rows = LT.journal_rows(root)
    last = LT.read_state(root, ('last_plan',)).get('last_plan') or {}
    execute = LT.load_settings(root)['execute']
    return {'key': 'longterm-virtual', 'label': '장기투자 가상 계좌', 'engine': '장기투자', 'kind': '가상 체결',
            'mode': '실행' if execute else '관찰', 'rows': rows,
            'holdings': {c: {'qty': p['qty'], 'avg': p['avg'], 'price': None, 'pnl': None} for c, p in ec.positions(rows).items()},
            'last': (f"{last['day']} {LT.why_label(last.get('why'))} · 주문 {len(last.get('orders') or [])}건"
                     f"{'' if execute else '(계획만)'}") if last.get('day') else '계획 전'}


def longterm_alerts(root, today):
    """Long-term engine items that need a look today: a skipped decision, a broken theme story, missing macro data."""
    root = Path(root)
    if not (root / 'longterm.sqlite3').exists():
        return []
    import vibe_longterm as LT
    from sqlite_ro import ro_connect
    found = []
    with ro_connect(root / 'longterm.sqlite3') as db:
        found += [f'장기투자 판단 건너뜀: {reason}' for (reason,) in db.execute(
            "SELECT reason FROM decisions WHERE kind = 'skip' AND day = ? ORDER BY time", (today,))]
    state = LT.read_state(root, ('plan', 'story'))
    broken = [LT.ASSETS[k][1] for k, v in ((state.get('story') or {}).get('stories') or {}).items()
              if v.get('status') == 'broken' and k in LT.ASSETS]
    if broken:
        found.append(f"장기투자 스토리 훼손: {', '.join(broken)} (위성 비중 0, 장기투자 → 테마 스토리)")
    plan = state.get('plan') or {}
    if plan.get('day') == today and (errors := (plan.get('macro') or {}).get('errors')):
        found.append(f"장기투자 거시 자료 일부 없음: {', '.join(errors)} (운영 → API 연동 → FRED)")
    return found


def latest_longterm(runtime):
    """{account key: the last longterm_live event} from nhplug/longterm-live.jsonl."""
    out = {}
    for event in ec.recent_events(Path(runtime) / 'longterm-live.jsonl', 500):
        if event.get('account'):
            out[event['account']] = event
    return out


def holding_spans(rows, now):
    """Per symbol, each stretch from the first buy to the sell that empties it (or `now` if still held), with its
    realized P&L; rows are one account's fills of one day (time, side, qty, 종목, pnl)."""
    spans, open_ = [], {}
    for r in sorted(rows, key=lambda r: r['time']):
        name, qty = r['종목'], int(r.get('qty') or 0)
        span = open_.get(name)
        if r['side'] == 'BUY':
            if span is None:
                span = open_[name] = {'종목': name, 'start': r['time'], 'end': None, 'qty': 0, 'pnl': 0.0}
            span['qty'] += qty
        elif span is not None:
            span['qty'] -= qty
            span['pnl'] += r.get('pnl') or 0
            if span['qty'] <= 0:
                span['end'] = r['time']
                spans.append(open_.pop(name))
    for span in open_.values():
        spans.append({**span, 'end': now})
    for span in spans:
        span['결과'] = '보유 중' if span['end'] == now else '이익' if span['pnl'] > 0 else '손실' if span['pnl'] < 0 else '본전'
    return spans


def heatmap(frame):
    """10-minute activity cells with the count written in; dark text on pale cells so a count of 1 stays readable."""
    import altair as alt
    top = max(int(frame['건수'].max()), 1)
    base = alt.Chart(frame).encode(x=alt.X('시각:O', title=None, axis=alt.Axis(labelAngle=-45)), y=alt.Y('구분:N', title=None))
    return (base.mark_rect(stroke='#0b1020').encode(color=alt.Color('건수:Q', scale=alt.Scale(scheme='purples', domainMin=0), legend=None),
                                                    tooltip=['구분', '시각', '건수'])
            + base.mark_text(fontSize=10).encode(text='건수:Q', color=alt.condition(f'datum["건수"] > {top / 2}',
                                                                                    alt.value('#f8fafc'), alt.value('#1e1b4b')))
            ).properties(height=200)


def activity(runtime, day, jev_root):
    """10-minute activity counts for the heatmap: Jev judgments, engine orders, live plans and blocks."""
    rows = []
    bucket = lambda stamp: stamp[11:15] + '0'
    for event in ec.recent_events(Path(jev_root) / 'events.jsonl', 8000):
        stamp, kind = str(event.get('time', '')), event.get('event')
        if not stamp.startswith(day):
            continue
        if kind in ('jev_shadow', 'jev_holding_decision'):
            rows.append(('Jev 판단', bucket(stamp)))
        elif kind in ('submitted', 'sell_submitted'):
            rows.append(('엔진 주문', bucket(stamp)))
        elif kind and kind.endswith('error'):
            rows.append(('오류', bucket(stamp)))
    for event in ec.recent_events(Path(runtime) / 'live-follow.jsonl', 8000):
        stamp = str(event.get('time', ''))
        if not stamp.startswith(day):
            continue
        if event.get('action') in ('BUY', 'SELL'):
            rows.append((f"{event.get('account')} 매매 계획", bucket(stamp)))
        if event.get('action') == 'BLOCK' or event.get('execution') == 'BLOCKED':
            rows.append(('실계좌 차단', bucket(stamp)))
    counts = {}
    for key in rows:
        counts[key] = counts.get(key, 0) + 1
    return [{'구분': k[0], '시각': k[1], '건수': n} for k, n in sorted(counts.items(), key=lambda kv: kv[0][1])]


class _AllHeld:
    """`owned` for reading the paper request file: every code counts, so reading never prunes or writes it."""
    def __contains__(self, code):
        return True


def stopped_in_session(runtime, states, now):
    """'{label}: 종료 (장중)' for each service that should stay up through the session (intraday_audit.SESSION_SERVICES,
    until its SERVICE_UNTIL or the runner's close) and is not running; after hours and on KRX holidays 종료 is normal."""
    import intraday_audit
    from automation_status import NAMES
    watched = [(name, NAMES.get(name, name)) for name in intraday_audit.SESSION_SERVICES]
    if not any(states.get(label) == '종료' for _, label in watched):
        return []  # nothing to say: no clock or holiday table needed
    import session_times
    from nh_paper_isolated import is_trading_day
    clock = now.strftime('%H:%M')
    close = session_times.load(Path(runtime) / 'shared-etf-paper')['close']
    if not ('09:05' <= clock < close and is_trading_day(now.date())):
        return []
    return [f'{label}: 종료 (장중)' for name, label in watched
            if states.get(label) == '종료' and clock < intraday_audit.SERVICE_UNTIL.get(name, '15:20')]


def loss_halts(runtime, records, today):
    """{live account key: True · False · None} of today's daily loss halt, from the rows the order guard itself checks
    (live_halts); None = it cannot be told (unreadable ledger, or a halt on an account .env does not name)."""
    keys, path = [k for k, r in records.items() if r.get('kind') == 'live'], Path(runtime) / 'live-orders.sqlite3'
    if not keys or not path.exists():
        return dict.fromkeys(keys, False)
    import hashlib
    import sqlite3
    from accounts import digest
    from dotenv import dotenv_values
    from local_env import ENV
    from nh_live_follow import configured_accounts
    from sqlite_ro import ro_connect
    try:
        with ro_connect(path) as db:
            halted = {scope for (scope,) in db.execute('SELECT scope FROM live_halts WHERE day=?', (today.replace('-', ''),))}
    except sqlite3.Error:
        return dict.fromkeys(keys)
    scopes = {digest(n): hashlib.sha256(('nh-live:' + n).encode()).hexdigest() for n in configured_accounts(dotenv_values(ENV, interpolate=False))}
    return {k: scopes[records[k].get('hash')] in halted if records[k].get('hash') in scopes else None if halted - set(scopes.values()) else False for k in keys}


def alerts(runtime, states, follow, today, now=None):
    """Things that need a look, in words: executors that stopped in the session or are in an unknown state, pending
    exit requests, live blocks and errors today, and external APIs failing or late today (api_status_view)."""
    found = []
    for label, state in states.items():
        if any(word in label for word in ('실행기', '판단기')) and state not in ('실행 중', '종료', '미등록', '확인 중'):
            found.append(f'{label}: {state}')
    runtime = Path(runtime)
    found += stopped_in_session(runtime, states, now or datetime.now().astimezone())
    from nh_live_follow import exit_requests as live_requests
    from nh_paper_isolated import exit_requests as paper_requests
    pending = [('모의계좌', paper_requests(runtime / 'shared-etf-paper', _AllHeld()))]  # the runners' own readers: a
    pending += [(key, live_requests(runtime, key)) for key in sorted(  # malformed item is ignored, never a TypeError
        p.stem.removeprefix('live-exit-requests-') for p in runtime.glob('live-exit-requests-*.json'))]
    found += [f"정리 요청 대기 · {who}: {', '.join(codes)}" for who, codes in pending if codes]
    blocked = {}
    for event in ec.recent_events(runtime / 'live-follow.jsonl', 8000):
        if str(event.get('time', '')).startswith(today) and (event.get('action') == 'BLOCK' or event.get('error_type')):
            blocked[event.get('account')] = blocked.get(event.get('account'), 0) + 1
    found += [f'{k} 미러 차단·오류 {n}회 (운영 → AI·실행 기록에서 확인)' for k, n in blocked.items()]
    for path in sorted((runtime / 'live-follow').glob('lag-alert-*.json')):  # nh_live_follow.mirror_lag (11/17)
        try:
            lag = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if str(lag.get('since', '')).startswith(today):
            found.insert(0, f"실계좌 미러 지연 · {path.stem.replace('lag-alert-', '')}: 모의보다 더 보유 {lag.get('excess')} · "
                            f"{lag.get('minutes')}분째 ({lag.get('last_reason') or lag.get('last_action')})")
    import accounts
    records = accounts.load(runtime / 'accounts.json')
    found += [f"{records[k].get('label', k)} 하루 손실 정지: 오늘 새 매수 없음" if on else f"{records[k].get('label', k)} 하루 손실 정지 여부 확인 불가"
              for k, on in loss_halts(runtime, records, today).items() if on is not False]
    found += longterm_alerts(runtime / 'vibe-longterm', today)
    if today == datetime.now().astimezone().strftime('%Y-%m-%d'):  # failing or late external APIs (API 연동 tab)
        import api_status_view
        found += api_status_view.current_alerts()
    return found


def today_realized(items, today):
    """(공유 모의계좌, 실계좌 합) of today's realized P&L for the two cards: the long-term books are 장기투자's."""
    pnl = lambda kind: sum(ec.summary(ec.in_period(i['rows'], today))['pnl'] for i in items if i['kind'] == kind)  # noqa: E731
    return pnl(PAPER_KIND), pnl('실계좌')


PAPER_KIND = '모의(공유)'  # the shared paper account item (accounts())


def realized_overview(items, now):
    """Today's realized P&L of every account (user 2026-11-18): [{계좌, 구분, 오늘 실현, 매도, 승률}] and a 합계 row last.
    Today only: summed fill-price sells over longer spans showed a profit while an account sat below its deposit, so
    the whole gain is the balance section's 총자산 − 원금. Costs are not included."""
    day = now.date().isoformat()
    out = []
    for item in items:
        sells = [r for r in item['rows'] if r.get('pnl') is not None and str(r['time'])[:10] == day]
        wins = sum(r['pnl'] > 0 for r in sells)
        out.append({'계좌': item['label'], '구분': item['kind'], '오늘 실현': round(sum(r['pnl'] for r in sells)),
                    '매도': len(sells), '승률': f'{wins / len(sells):.0%}' if sells else '—', '_wins': wins})
    sold, wins = sum(r['매도'] for r in out), sum(r.pop('_wins') for r in out)
    return out + [{'계좌': '합계', '구분': '', '오늘 실현': sum(r['오늘 실현'] for r in out), '매도': sold,
                   '승률': f'{wins / sold:.0%}' if sold else '—'}]


def realized_section(items, now):
    """종합 현황 '오늘 실현손익 · 전체 계좌' (user 2026-11-18): today's sells of every account, signed and coloured, with a 합계 row."""
    import pandas as pd
    import streamlit as st
    from dialogs import _tone, table_frame
    rows = realized_overview(items, now)
    st.markdown(f"##### 오늘 실현손익 · 전체 계좌 :gray[· 합계 {rows[-1]['오늘 실현']:+,}원]",
                help='오늘 매도 체결의 실현손익 합입니다(체결가 기준, 수수료·세금 미반영). 실계좌는 실체결이 없는 날 가상 추정이 섞일 수 있습니다. '
                     '계좌 전체가 이익인지 손실인지는 위 잔고 현황의 전체 손익(총자산 − 원금)을 봅니다.')
    frame = pd.DataFrame([{**r, '오늘 실현': f"{r['오늘 실현']:+,}원"} for r in rows])
    table_frame(frame.style.map(_tone, subset=['오늘 실현']))


def balance_records(path):
    """Read usable total-asset observations from the dashboard balance log (impossible ones skipped, live_fills.plausible)."""
    from live_fills import plausible
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if plausible(row) and isinstance(row.get('time'), str):
            yield row


def balance_snapshots(path, today):
    """(latest row, today's first row) of a balance log."""
    last = first = None
    for row in balance_records(path):
        last = row
        if first is None and row['time'].startswith(today):
            first = row
    return last, first


def balance_overview(items, runtime, today, principals=None):
    """Balances of every account (user 2026-11-18): [{계좌, 구분, 총자산, 원금, 전체 손익, 전체 수익률, 매입, 평가손익,
    보유 수익률, 오늘 변동, 기준}] from the records the runners keep (no broker call), then '실계좌 합계' and '전체 합계'.
    전체 손익 = 총자산 − 원금 (the money put in, `principals` {item key: won}, set on the screen), so fees, taxes and
    trades outside the ledger all count; an account without a principal shows no whole gain. The long-term virtual
    account's 원금 is the 1억 start plus the 적립 paid in (last close). 평가손익 is the holdings alone."""
    import vibe_longterm as LT
    runtime, out, principals = Path(runtime), [], principals or {}
    rate = lambda gain, base: round(gain / base * 100, 4) if base else None
    for item in items:
        last = first = None
        principal = principals.get(item['key'])
        if item['key'] == 'jev':
            last, first = balance_snapshots(runtime / 'shared-etf-paper' / 'account-snapshots.jsonl', today)
        elif item['key'] == 'longterm-virtual':
            state = LT.read_state(runtime / 'vibe-longterm', ('nav_latest', 'dca:actual'))
            nav = state.get('nav_latest') or {}
            if isinstance(nav.get('actual'), (int, float)):
                principal = LT.CAPITAL + (nav.get(LT.CONTRIBUTED) or 0)
                last = {'time': f"{nav.get('day', '')}", 'total_assets': nav['actual']}
                from vibe_longterm_view import holdings
                held, cash = holdings(runtime / 'vibe-longterm')
                if cash is not None:  # 매입 = the holdings' cost; 평가 = the close's account value less its cash
                    # shortcut: cash is now and nav is the last close, so a trade since the close counts at cost (and a
                    # 적립 paid in since is taken back out) until the 15:40 close prices it
                    paid = (state.get('dca:actual') or {}).get('contributed_krw')
                    paid_since = max(0, paid - (nav.get(LT.CONTRIBUTED) or 0)) if isinstance(paid, (int, float)) else 0
                    last.update(invested=round(sum(q * avg for q, avg in held.values())), evaluation=nav['actual'] - (cash - paid_since))
        elif item['kind'] == '실계좌':
            last, first = balance_snapshots(runtime / f"live-balance-{item['key']}.jsonl", today)
        realized = round(sum(r['pnl'] for r in item.get('rows') or [] if r.get('pnl') is not None and str(r.get('time', ''))[:10] == today))
        row = {'계좌': item['label'], '구분': item['kind'], '오늘 실현': realized, '총자산': None, '원금': principal or None, '전체 손익': None,
               '전체 수익률': None, '매입': None, '평가손익': None, '보유 수익률': None, '오늘 변동': None, '기준': '기록 없음'}
        if last:
            invested, evaluation, assets = last.get('invested'), last.get('evaluation'), last['total_assets']
            pnl = evaluation - invested if isinstance(invested, (int, float)) and isinstance(evaluation, (int, float)) else None
            stamp = str(last['time'])
            row.update({'총자산': assets, '매입': invested, '평가손익': pnl,
                        '전체 손익': assets - principal if principal else None, '전체 수익률': rate(assets - principal, principal) if principal else None,
                        '보유 수익률': rate(pnl, invested) if pnl is not None else None,
                        '오늘 변동': assets - first['total_assets'] if first else None,
                        '기준': f'{stamp[5:10]} 마감' if len(stamp) == 10 else stamp[11:16] if stamp.startswith(today) else stamp[5:16].replace('T', ' ')})
        out.append(row)

    def total(name, rows):
        known = [r for r in rows if r['총자산'] is not None]
        based = [r for r in known if r['원금']]
        def add(field, group):  # None when no account knows it: a '+0원' total under rows of '—' read as known
            values = [r[field] for r in group if r[field] is not None]
            return sum(values) if values else None
        invested, pnl, principal, gain = add('매입', known), add('평가손익', known), add('원금', based), add('전체 손익', based)
        return {'계좌': name, '구분': '' if len(based) == len(rows) else f'{len(based)}/{len(rows)} 계좌 기준', '오늘 실현': add('오늘 실현', rows),
                '총자산': add('총자산', known) if known else None, '원금': principal or None,
                '전체 손익': gain if based else None, '전체 수익률': rate(gain, principal) if based else None,
                '매입': invested, '평가손익': pnl if known else None, '보유 수익률': rate(pnl, invested) if pnl is not None else None,
                '오늘 변동': add('오늘 변동', known) if known else None, '기준': ''}
    live = [r for r in out if r['구분'] == '실계좌']
    return out + ([total('실계좌 합계', live)] if live else []) + [total('전체 합계', out)]


PRINCIPAL_RECORD = {'jev': 'paper-shared'}  # balance item key → accounts.json key (live accounts share the key)


def principals_of(records):
    """{balance item key: 원금} from accounts.json principal_krw (entered on 종합 현황; unset = no whole gain)."""
    keys = {'jev': 'paper-shared', **{k: k for k, r in records.items() if r.get('kind') == 'live'}}
    return {item: records[key]['principal_krw'] for item, key in keys.items()
            if key in records and isinstance(records[key].get('principal_krw'), (int, float)) and records[key]['principal_krw'] > 0}


def principal_form(items, records):
    """'원금 설정': the money put into each account, saved to accounts.json principal_krw (settings history kept)."""
    import streamlit as st
    import accounts as A
    current = principals_of(records)
    with st.expander('원금 설정 · 전체 수익률 기준', icon=':material/savings:', expanded=any(
            i['key'] not in current and i['key'] != 'longterm-virtual' for i in items)):
        st.caption('계좌에 넣은 돈(입금 합계 − 출금 합계)입니다. 전체 손익 = 총자산 − 원금이라 수수료·세금까지 반영됩니다. '
                   '입금·출금을 하면 여기서 고쳐 주세요. 장기투자 가상 계좌는 1억 + 적립 누계로 자동 계산합니다.')
        editable = [i for i in items if i['key'] != 'longterm-virtual']
        with st.form('principal_form', border=False):
            values = {}
            for col, item in zip(st.columns(len(editable) or 1), editable):
                values[item['key']] = col.number_input(f"{item['label']} 원금(원)", min_value=0, step=10_000,
                                                       value=int(current[item['key']]) if item['key'] in current else None,
                                                       placeholder='입력 필요', key=f"principal_{item['key']}")
            if st.form_submit_button('원금 저장', type='primary', icon=':material/save:'):
                try:
                    for key, value in values.items():
                        if value and value != current.get(key):
                            A.save_one(PRINCIPAL_RECORD.get(key, key), {'principal_krw': int(value)})
                except (OSError, ValueError) as exc:
                    st.error(f'저장하지 못했습니다: {exc}')
                else:
                    st.rerun()


def balance_section(items, runtime, today, refresh=None, records=None):
    """종합 현황 '잔고 현황 · 계좌별 · 전체' (user 2026-11-18): a card per account and the total, signed and coloured, then
    the table. `refresh` reads the live balances from the broker once (read-only) when a live record is missing or old."""
    import pandas as pd
    import streamlit as st
    from dialogs import _tone, table_frame
    principals = principals_of(records or {})
    rows = balance_overview(items, runtime, today, principals)
    won = lambda v: '확인 불가' if v is None else f'{v:,.0f}원'
    signed_won = lambda v: '—' if v is None else f'{v:+,.0f}원'
    head, button = st.columns([4, 1], vertical_alignment='center')
    head.markdown('##### 잔고 현황 · 계좌별 · 전체',
                  help='카드 아래 줄은 오늘 실현손익(오늘 매도, 체결가 기준)입니다. 계좌 전체가 이익인지 손실인지는 표의 전체 손익·전체 수익률'
                       '(총자산 − 원금, 수수료·세금 포함)에서 봅니다. 원금은 아래 "원금 설정"에서 정합니다. 표의 보유 수익률은 지금 보유한 종목만의 평가손익 ÷ 매입금액입니다. 모의계좌는 실행기의 5분 잔고 기록, 실계좌는 미러·장기 실행기의 '
                       '5분 잔고 기록, 장기투자 가상 계좌는 최근 마감 순자산(원금 = 1억 + 적립 누계)입니다.')
    if refresh and button.button('실계좌 잔고 조회', icon=':material/sync:', width='stretch', key='overview_live_balance',
                                 help='증권사에서 실계좌 잔고를 한 번 조회해 잔고 기록에 남깁니다(조회 전용, 주문 없음).'):
        for problem in refresh():
            st.warning(problem)
        rows = balance_overview(items, runtime, today, principals)
    cards = []
    for r in rows:
        gain = r['오늘 실현']  # user 2026-11-18: the cards show today's realized P&L; the whole gain is in the table below
        tone = sc.signed(gain)
        mark = '▲ ' if gain > 0 else '▼ ' if gain < 0 else ''
        cards.append({'label': r['계좌'], 'value': won(r['총자산']), 'tone': tone, 'signed_sub': True, 'icon': 'functions' if '합계' in r['계좌'] else 'account_balance_wallet',
                      'sub': f"오늘 실현 {mark}{signed_won(gain)}" + ('' if r['총자산'] is not None else f" · 잔고 {r['기준']}")
                             + (f" · {r['기준']}" if r['기준'] and r['총자산'] is not None else '') + (f" · {r['구분']}" if '합계' in r['계좌'] and r['구분'] else '')})
    for start in range(0, len(cards), 3):  # three a row: a 50억 paper balance does not fit a sixth of the width
        sc.row(cards[start:start + 3], 3)
    if any(r['구분'] == '실계좌' and r['총자산'] is None for r in rows):
        st.caption('잔고 기록이 없는 실계좌는 "실계좌 잔고 조회"로 한 번 읽어 오세요. 미러·장기 실행기가 다음에 시작한 뒤부터는 5분마다 자동으로 기록됩니다.')
    rate = lambda v: '—' if v is None else f'{v:+.2f}%' if abs(v) >= 0.01 or v == 0 else f'{v:+.4f}%'  # 50억 moves in thousandths
    frame = pd.DataFrame([{**r, '총자산': won(r['총자산']), '매입': '—' if r['매입'] is None else f"{r['매입']:,.0f}원",
                           '원금': '입력 필요' if r['원금'] is None else f"{r['원금']:,.0f}원",
                           **{k: signed_won(r[k]) for k in ('오늘 실현', '전체 손익', '평가손익', '오늘 변동')},
                           '전체 수익률': rate(r['전체 수익률']), '보유 수익률': rate(r['보유 수익률'])} for r in rows])
    frame = frame[['계좌', '구분', '총자산', '원금', '전체 손익', '전체 수익률', '오늘 실현', '오늘 변동', '매입', '평가손익', '보유 수익률', '기준']]
    table_frame(frame.style.map(_tone, subset=['전체 손익', '전체 수익률', '오늘 실현', '평가손익', '보유 수익률', '오늘 변동']))
    principal_form(items, records or {})


def render(runtime, records, states, names, refresh_balances=None):
    import altair as alt
    import pandas as pd
    import streamlit as st
    from engine_roles import ledger_root
    runtime, now = Path(runtime), datetime.now().astimezone()
    today = now.strftime('%Y-%m-%d')
    head, button = st.columns([4, 1], vertical_alignment='center')
    head.subheader('종합 현황')
    if button.button('다시 읽기', icon=':material/refresh:', width='stretch', key='overview_refresh',
                     help='원장·실행기 기록을 다시 읽습니다(증권사 조회 없음).'):
        ec._cached_journal.cache_clear()
        ec._cached_live.cache_clear()
    items = accounts(runtime, records, now, names)
    st.caption(f'모의계좌·실계좌를 한 화면에 모았습니다 · 원장과 실행기 기록 기준(증권사 조회 없음) · {now:%H:%M:%S} · '
               '수수료·세금 미반영 · 실계좌는 실체결이 없는 날 가상 추정(시황별 1% 손절 미반영)')

    day = {i['key']: ec.summary(ec.in_period(i['rows'], today)) for i in items}
    total = {i['key']: ec.summary(i['rows']) for i in items}
    engine_pnl, live_pnl = today_realized(items, today)
    trades = sum(day[k]['trades'] for k in day)
    buys, sells = sum(day[k]['buys'] for k in day), sum(day[k]['sells'] for k in day)
    held = sum(len(i['holdings']) for i in items)
    watched = {k: v for k, v in states.items() if any(w in k for w in ('실행기', '판단기'))}
    running = sum(v == '실행 중' for v in watched.values())
    follow = latest_follow(runtime)
    warnings = alerts(runtime, states, follow, today)
    sc.row([{'label': '오늘 실현 · 모의계좌', 'value': f'{engine_pnl:+,}원', 'tone': sc.signed(engine_pnl), 'icon': 'science',
             'help': '공유 모의계좌(Jev)의 오늘 실현손익(체결가 기준, 비용 미반영)입니다. 장기투자는 "장기투자" 탭에서 봅니다.'},
            {'label': '오늘 실현 · 실계좌', 'value': f'{live_pnl:+,}원', 'tone': sc.signed(live_pnl), 'icon': 'account_balance',
             'sub': '실체결이 없으면 가상 추정', 'help': '실계좌들의 오늘 실현손익 합입니다. 실체결이 없는 날은 엔진 원장·계좌 기본 손절 기반 추정이며 시황별 1% 손절은 반영하지 않습니다.'},
            {'label': '오늘 거래', 'value': f'{trades}건', 'tone': 'ai' if trades else 'off', 'icon': 'swap_vert',
             'sub': f'매수 {buys} · 매도 {sells}', 'pulse': bool(trades)},
            {'label': '보유', 'value': f'{held}건', 'tone': 'up' if held else 'off', 'icon': 'inventory_2',
             'sub': ', '.join(f"{i['label']} {len(i['holdings'])}" for i in items if i['holdings']) or '없음'},
            {'label': '실행기 · 주의', 'value': '확인 중' if '확인 중' in watched.values() else f'{running}/{len(watched)} 실행', 'icon': 'monitor_heart',
             'tone': 'alert' if warnings else 'off' if '확인 중' in watched.values() else 'ok', 'sub': f'주의 {len(warnings)}건' if warnings else '주의 없음',
             'help': '실행기·판단기 중 실행 중인 수(장 밖에는 종료가 정상)와 확인이 필요한 항목 수입니다.'}], ask='summary')
    todays_all = [{**r, '계좌': i['label'], 'kind': i['kind']} for i in items for r in ec.in_period(i['rows'], today)]
    detail = st.columns(5)
    if detail[0].button('자세히 · 모의계좌 실현', key='ov_d_paper', icon=':material/open_in_new:', width='stretch'):
        realized_dialog('오늘 실현 · 모의계좌', [r for r in todays_all if r['kind'] == PAPER_KIND], names)
    if detail[1].button('자세히 · 실계좌 실현', key='ov_d_live', icon=':material/open_in_new:', width='stretch'):
        realized_dialog('오늘 실현 · 실계좌', [r for r in todays_all if r['kind'] == '실계좌'], names)
    if detail[2].button('자세히 · 오늘 거래', key='ov_d_trades', icon=':material/open_in_new:', width='stretch'):
        trades_dialog(todays_all, names)
    if detail[3].button('자세히 · 보유', key='ov_d_held', icon=':material/open_in_new:', width='stretch'):
        holdings_dialog(items, names)
    if detail[4].button('자세히 · 실행기·주의', key='ov_d_alerts', icon=':material/open_in_new:', width='stretch'):
        alerts_dialog(watched, warnings, follow)
    balance_section(items, runtime, today, refresh_balances, records)
    realized_section(items, now)

    import market_context
    from market_view import render_summary

    render_summary(market_context.load())
    st.markdown('##### 계좌별 요약')
    table = pd.DataFrame([{
        '계좌': i['label'], '엔진': i['engine'], '구분': i['kind'] + ('' if i['kind'] != '실계좌' else f" · {i['mode']}"),
        '오늘 매수·매도': f"{day[i['key']]['buys']} · {day[i['key']]['sells']}", '오늘 실현': ec._won(day[i['key']]['pnl']),
        '오늘 수익률': ec._pct(day[i['key']]['return_pct']), '누적 실현': ec._won(total[i['key']]['pnl']),
        '누적 승률': f"{total[i['key']]['wins']}/{total[i['key']]['sells']}" if total[i['key']]['sells'] else '—',
        '보유': ', '.join(f"{names.get(c, c)} {h['qty']:,}주" for c, h in i['holdings'].items()) or '없음',
        '마지막 활동': i['last']} for i in items])
    st.dataframe(table.style.map(sc.cell_tone, subset=['오늘 실현', '오늘 수익률', '누적 실현']), hide_index=True, width='stretch')

    labels = [i['label'] for i in items]
    color = alt.Color('계좌:N', scale=alt.Scale(domain=labels, range=PALETTE[:len(labels)]), legend=alt.Legend(orient='top'))
    kind_of = {i['label']: i['kind'] for i in items}
    todays = [{**r, '계좌': i['label'], 'engine': i['engine'], '종목': names.get(r['symbol'], r['symbol'])}
              for i in items for r in ec.in_period(i['rows'], today)]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('###### 오늘 누적 실현손익')
        sold = [r for r in todays if r['pnl'] is not None]
        if sold:
            frame = pd.DataFrame(sold).sort_values('time')
            frame['시각'] = pd.to_datetime(frame['time'], format='ISO8601')
            frame['누적'] = frame.groupby('계좌')['pnl'].cumsum()
            frame['구분'] = frame['계좌'].map(kind_of)
            base = alt.Chart(frame).encode(x=alt.X('시각:T', title=None, axis=alt.Axis(format='%H:%M')), color=color)
            st.altair_chart((alt.Chart(pd.DataFrame({'y': [0]})).mark_rule(color='#475569', strokeDash=[4, 4]).encode(y='y:Q')
                             + base.mark_line(interpolate='step-after', strokeWidth=2.5).encode(
                                 y=alt.Y('누적:Q', title='원', axis=alt.Axis(format=',.0f')),
                                 strokeDash=alt.StrokeDash('구분:N', legend=None))
                             + base.mark_point(filled=True, size=60).encode(
                                 y='누적:Q', tooltip=['계좌', '종목', 'path', alt.Tooltip('pnl:Q', title='손익', format='+,.0f')])
                             ).properties(height=230), width='stretch')
            st.caption('실계좌는 금액이 작아(1주 기준) 0 근처에 보일 수 있습니다. 실계좌만 보려면 실계좌 탭을 쓰세요.')
        else:
            st.caption('오늘 매도 기록이 없습니다.')
    with c2:
        st.markdown('###### 보유 현황 (매입금액)')
        hold = [{'계좌': i['label'], '종목': names.get(c, c), '매입금액': (h['avg'] or 0) * h['qty'], '수량': h['qty'],
                 '평가손익': h['pnl']} for i in items for c, h in i['holdings'].items()]
        if hold:
            st.altair_chart(alt.Chart(pd.DataFrame(hold)).mark_bar().encode(
                y=alt.Y('계좌:N', title=None, sort=labels), x=alt.X('매입금액:Q', title='원', axis=alt.Axis(format=',.0f')),
                color=alt.Color('종목:N', legend=alt.Legend(orient='top', title=None)),
                tooltip=['계좌', '종목', '수량', alt.Tooltip('매입금액:Q', format=',.0f'), alt.Tooltip('평가손익:Q', format='+,.0f')]
            ).properties(height=200), width='stretch')
        else:
            st.caption('지금 보유한 종목이 없습니다.')
    with st.container():
        head, pick = st.columns([1, 2], vertical_alignment='bottom')
        head.markdown('###### 오늘 거래 타임라인')
        traded = [l for l in labels if any(r['계좌'] == l for r in todays)]
        if traded:
            who = pick.segmented_control('계좌', traded, default=traded[0], key='ov_timeline_account', label_visibility='collapsed') or traded[0]
            mine = [r for r in todays if r['계좌'] == who]
            stamp = now.isoformat(timespec='seconds')
            spans = pd.DataFrame(holding_spans(mine, stamp), columns=['종목', 'start', 'end', 'qty', 'pnl', '결과'])  # no bar when only a carried holding was sold
            fills = pd.DataFrame(mine)
            fills['구분'] = fills['side'].map({'BUY': '매수 ▲', 'SELL': '매도 ▼'})
            lanes = sorted(fills['종목'].unique(), key=lambda n: min(r['time'] for r in mine if r['종목'] == n))
            x = alt.X('start:T', title=None, axis=alt.Axis(format='%H:%M', grid=True),
                      scale=alt.Scale(domain=[f'{today}T09:00:00', f'{today}T15:30:00']))
            y = alt.Y('종목:N', title=None, sort=lanes, axis=alt.Axis(labelLimit=140))
            bars = alt.Chart(spans).mark_bar(height=14, cornerRadius=4, opacity=0.45).encode(
                x=x, x2='end:T', y=y,
                color=alt.Color('결과:N', scale=alt.Scale(domain=['이익', '손실', '본전', '보유 중'], range=['#ef4444', '#3b82f6', '#94a3b8', '#f59e0b']),
                                legend=alt.Legend(orient='top', title=None)),
                tooltip=['종목', '결과', alt.Tooltip('pnl:Q', title='실현손익', format='+,.0f'),
                         alt.Tooltip('start:T', title='첫 매수', format='%H:%M'), alt.Tooltip('end:T', title='끝', format='%H:%M')])
            marks = alt.Chart(fills).mark_point(filled=True, size=110, opacity=1, stroke='#0f172a', strokeWidth=0.6).encode(
                x=alt.X('time:T'), y=y,
                shape=alt.Shape('구분:N', scale=alt.Scale(domain=['매수 ▲', '매도 ▼'], range=['triangle-up', 'triangle-down']), legend=None),
                color=alt.Color('구분:N', scale=alt.Scale(domain=['매수 ▲', '매도 ▼'], range=['#dc2626', '#2563eb']), legend=None),
                tooltip=['종목', '구분', alt.Tooltip('qty:Q', title='수량'), alt.Tooltip('price:Q', title='가격', format=',.0f'),
                         alt.Tooltip('time:T', title='시각', format='%H:%M:%S'), alt.Tooltip('path:N', title='판단 경로')])
            st.altair_chart(alt.layer(bars, marks).resolve_scale(color='independent').properties(height=46 * len(lanes) + 60),
                            width='stretch')
            st.caption('종목마다 한 줄: 막대는 첫 매수부터 다 판 시각까지(빨강 이익·파랑 손실·주황 보유 중), ▲ 매수 · ▼ 매도. '
                       '점에 마우스를 올리면 수량·가격·판단 경로가 보입니다. 실계좌는 엔진 원장의 미러라 모의와 같은 시각에 찍힙니다.')
        else:
            st.caption('오늘 거래가 없습니다.')
    with st.container():
        st.markdown('###### 판단·실행 활동 (10분 단위)')
        heat = activity(runtime, today, ledger_root(runtime, 'jev'))
        if heat:
            frame = pd.DataFrame(heat)
            frame['구분'] = frame['구분'].map(lambda k: k.replace('live-1', '실계좌 1').replace('live-2', '실계좌 2'))
            st.altair_chart(heatmap(frame), width='stretch')
        else:
            st.caption('오늘 활동 기록이 없습니다.')

    trades_tab, judge_tab, alert_tab = st.tabs([f'오늘 거래 전체 {len(todays)}', '최근 판단·계획', f'주의 사항 {len(warnings)}'])
    with trades_tab:
        if todays:
            frame = pd.DataFrame([{'시각': r['time'][11:19], '계좌': r['계좌'], '엔진': r['engine'],
                                   '종목': r['종목'], '구분': '매수 ▲' if r['side'] == 'BUY' else '매도 ▼', '수량': f"{r['qty']:,}주",
                                   '가격': f"{r['price']:,.0f}원", '매수금액': f"{r['amount']:,.0f}원" if r['side'] == 'BUY' else '',
                                   '매도금액': f"{r['amount']:,.0f}원" if r['side'] == 'SELL' else '',
                                   '수익률': ec._pct(r['return_pct']) if r['return_pct'] is not None else '',
                                   '실현손익': ec._won(r['pnl']) if r['pnl'] is not None else '', '판단 경로': r['path'],
                                   '판단 근거': r['basis'], '기록': r.get('source', '원장')}
                                  for r in sorted(todays, key=lambda r: r['time'], reverse=True)])
            st.dataframe(frame.style.map(sc.cell_tone, subset=['수익률', '실현손익']).map(sc.side_tone, subset=['구분']),
                         hide_index=True, width='stretch',
                         column_config={'판단 근거': st.column_config.TextColumn(width='large')})
            st.caption('매수·매도금액은 체결 수량 × 체결 가격이며 수수료·세금은 포함하지 않습니다. 실계좌 가상 기록은 추정 금액입니다.')
        else:
            st.caption('오늘 거래가 없습니다.')
    with judge_tab:
        candidate, table = ec.opinions(ec.recent_events(ledger_root(runtime, 'jev') / 'events.jsonl'), None)  # intraday vibe retired 2026-11-17
        from etf_universe import SYMBOLS
        cards = []
        if candidate:
            choice = candidate.get('choice')
            cards.append({'label': 'Jev 최근 후보', 'value': '대기' if choice == 'WAIT' else names.get(SYMBOLS.get(choice, choice), choice),
                          'tone': 'ai', 'icon': 'psychology', 'sub': f"신뢰도 {candidate.get('confidence', 0):.2f} · {L.clock(candidate.get('time'), today)}"})
        cards.append(longterm_card(runtime))
        for key, event in follow.items():
            if (records.get(key) or {}).get('engine') == 'longterm':  # its mirror retired when it moved to the long-term engine
                continue
            label = (records.get(key) or {}).get('label', key)
            cards.append({'label': f'{label} 미러', 'value': L.ACTIONS.get(event.get('action'), event.get('action', '—')),
                          'tone': 'up' if event.get('action') == 'BUY' else 'down' if event.get('action') == 'SELL' else 'off',
                          'icon': 'sync_alt', 'sub': f"{L.clock(event.get('time'), today)} · {L.reason(event.get('reason'))}"})
        if cards:
            sc.row(cards)
        if table:
            st.dataframe(pd.DataFrame([{'종목': names.get(c, c), 'Jev 확률': r.get('jev_prob')}
                                       for c, r in sorted(table.items(), key=lambda kv: -(kv[1].get('jev_prob') or 0))]),
                         hide_index=True, width='stretch',
                         column_config={'Jev 확률': st.column_config.ProgressColumn(format='%.2f', min_value=0.0, max_value=1.0)})
        st.caption('Jev 판단 근거와 실행 과정은 "운영 → AI·실행 기록", 장기투자 판단은 "장기투자" 탭에서 봅니다.')
    with alert_tab:
        if warnings:
            for line in warnings:
                st.warning(line, icon=':material/warning:')
        else:
            st.success('확인이 필요한 항목이 없습니다.', icon=':material/check_circle:')


def render_orders(runtime, records, states, names):
    """주문·정리 종합: holdings, today's orders/fills and pending exit requests of every account at once (local records)."""
    import pandas as pd
    import streamlit as st
    runtime, now = Path(runtime), datetime.now().astimezone()
    today = now.strftime('%Y-%m-%d')
    items = accounts(runtime, records, now, names)
    warnings = [w for w in alerts(runtime, states, latest_follow(runtime), today) if w.startswith('정리 요청')]
    st.subheader('주문·정리 종합')
    st.caption('모든 계좌의 보유·오늘 체결·정리 요청을 원장과 실행기 기록으로 모았습니다(증권사 조회 없음). '
               '증권사 잔고·미체결 확인과 정리 요청은 위 "보기"에서 계좌 이름을 고른 뒤 그 계좌의 "주문·정리" 탭에서 합니다.')
    cards = []
    for i in items:
        day = ec.summary(ec.in_period(i['rows'], today))
        cards.append({'label': i['label'], 'value': f"보유 {len(i['holdings'])}종목", 'icon': 'inventory_2',
                      'tone': 'up' if i['holdings'] else 'off',
                      'sub': f"오늘 매수 {day['buys']} · 매도 {day['sells']} · {i['kind']}" + (f" {i['mode']}" if i['kind'] == '실계좌' else '')})
    sc.row(cards)
    if warnings:
        for line in warnings:
            st.warning(line, icon=':material/pending_actions:')
    hold_tab, fill_tab, plan_tab = st.tabs(['보유 전체', '오늘 체결 전체', '장기투자 오늘 주문 계획'])
    with plan_tab:
        plans = longterm_plan_rows(runtime, records, today)
        if plans:
            st.dataframe(pd.DataFrame(plans), hide_index=True, width='stretch', alt='장기투자 오늘 주문 계획')
        st.caption('장기 엔진의 가상 계좌 계획과 장기투자 실계좌의 하루 주문 계획입니다(조회 전용). 가상 계좌는 "가상 체결" 스위치, '
                   '실계좌는 실주문 스위치가 켜져야 실제로 체결합니다.' if plans else '오늘 장기투자 주문 계획이 없습니다(평일 09:40 이후 계획).')
    with hold_tab:
        rows = [{'계좌': i['label'], '종목': names.get(c, c), '수량': f"{h['qty']:,}주",
                 '평균가': f"{h['avg']:,.0f}원" if h['avg'] else '—', '현재가': f"{h['price']:,.0f}원" if h['price'] else '—',
                 '평가손익': ec._won(h['pnl']) if h['pnl'] is not None else '—'}
                for i in items for c, h in i['holdings'].items()]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch') if rows else st.caption('보유 종목이 없습니다.')
    with fill_tab:
        rows = [{'시각': r['time'][11:19], '계좌': i['label'], '종목': names.get(r['symbol'], r['symbol']),
                 '구분': '매수 ▲' if r['side'] == 'BUY' else '매도 ▼', '수량': f"{r['qty']:,}주", '가격': f"{r['price']:,.0f}원",
                 '실현손익': ec._won(r['pnl']) if r['pnl'] is not None else '', '사유': r['path'], '기록': r.get('source', '원장')}
                for i in items for r in ec.in_period(i['rows'], today)]
        rows.sort(key=lambda r: r['시각'], reverse=True)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch') if rows else st.caption('오늘 체결이 없습니다.')


def longterm_plan_rows(runtime, records, today):
    """Today's planned orders of the long-term virtual account and of each long-term live account, as one table."""
    runtime = Path(runtime)
    rows = []
    root = runtime / 'vibe-longterm'
    if (root / 'longterm.sqlite3').exists():
        import vibe_longterm as LT
        last = LT.read_state(root, ('last_plan',)).get('last_plan') or {}
        if last.get('day') == today:
            rows += [{'계좌': '장기투자 가상 계좌', '종목': LT.asset_name(o.get('key')),
                      '매수/매도': '매수' if o.get('side') == 'BUY' else '매도', '수량': o.get('qty'), '가격': f"{o.get('price', 0):,}원",
                      '상태': '가상 체결' if last.get('execute') else '계획만(dry-run)', '사유': LT.why_label(last.get('why'))}
                     for o in last.get('orders') or []]
    from accounts import longterm_accounts
    longterm = longterm_accounts(records)
    if longterm:
        import longterm_account_view as LV
        import longterm_live as LL
        for key, record in longterm.items():
            day = LL.load_state(runtime, key)
            if str(day.get('day', '')) == today:
                rows += [{'계좌': record.get('label', key), '종목': r['종목'], '매수/매도': r['매수/매도'], '수량': r['수량'], '가격': r['가격'],
                          '상태': r['상태'], '사유': '장기 엔진 계획'} for r in LV.order_rows(day)]
    return rows


def record_days(runtime, today):
    """Days with records in the journals timeline() reads (the Jev runner, the live mirror, the long-term engine and its
    live executor), today included, newest first."""
    import re
    from engine_roles import ledger_root
    runtime = Path(runtime)
    events = [*ec.recent_events(ledger_root(runtime, 'jev') / 'events.jsonl', 12000), *ec.recent_events(runtime / 'live-follow.jsonl', 8000),
              *ec.recent_events(runtime / 'longterm-live.jsonl', 2000)]
    days = {str(e.get('time', ''))[:10] for e in events if isinstance(e, dict)} | {today}
    path = runtime / 'vibe-longterm' / 'longterm.sqlite3'
    if path.exists():
        from sqlite_ro import ro_connect
        with ro_connect(path) as db:
            days |= {day for (day,) in db.execute('SELECT DISTINCT day FROM decisions')}
    return sorted((d for d in days if re.fullmatch(r'\d{4}-\d\d-\d\d', str(d))), reverse=True)


def timeline(runtime, day, names, reason_label=str):
    """One day's judgments and executions of every engine and live account, oldest first:
    [{time, 출처, 종류, 종목, 내용, 값}] plus {wait reason: count} of the engine runners' refused entries."""
    from engine_roles import ledger_root
    from etf_universe import SYMBOLS
    runtime = Path(runtime)
    name = lambda code: names.get(SYMBOLS.get(code, code), code) if code else ''
    rows, waits = [], {}
    add = lambda event, source, kind, symbol, text, value=None: rows.append(
        {'time': str(event.get('time', '')), '출처': source, '종류': kind, '종목': name(symbol), '내용': text, '값': value})
    for engine in ('jev',):  # the intraday vibe ledger moved to the archive (2026-11-19)
        root, label = ledger_root(runtime, engine), ec.ENGINES[engine]
        for event in ec.recent_events(root / 'events.jsonl', 12000):
            if not str(event.get('time', '')).startswith(day):
                continue
            kind = event.get('event') or ''
            if kind == 'jev_shadow':
                c = ((event.get('jev') or {}).get('candidate') or {})
                if c:
                    add(event, 'Jev', '판단', None if c.get('choice') == 'WAIT' else c.get('choice'),
                        f"후보 {'대기' if c.get('choice') == 'WAIT' else name(c.get('choice'))} · 신뢰도 {c.get('confidence', 0):.2f}",
                        c.get('confidence'))
            elif kind == 'jev_holding_decision':
                add(event, 'Jev', '보유 판단', event.get('symbol'), f"{event.get('choice')} · 신뢰도 {event.get('confidence', 0):.2f}",
                    event.get('confidence'))
            elif kind in ('v2_entry_gate', 'v2_execution_gate'):
                if event.get('permitted') is True:
                    add(event, f'{label} 실행기', '진입 허용', event.get('choice'), PATHS_LABEL(event.get('source')))
                elif event.get('permitted') is False:
                    raw = event.get('reason') or '기록 없음'
                    reason = WAIT_REASONS.get(raw) or reason_label(raw)
                    waits[reason] = waits.get(reason, 0) + 1
            elif kind in ('submitted', 'sell_submitted', 'split_add'):
                side = '매도' if kind == 'sell_submitted' else '매수'
                why = ec.EXITS.get(event.get('trigger'), event.get('trigger')) if side == '매도' else PATHS_LABEL(event.get('source'))
                add(event, f'{label} 실행기', f'{side} 주문', event.get('symbol'),
                    f"{event.get('qty', '')}주 · 지정가 {event.get('limit', 0):,}원" + (f' · {why}' if why else ''))
            elif kind in ('v2_risk_exit', 'split_take_profit', 'manual_exit'):
                add(event, f'{label} 실행기', '청산 판단', event.get('symbol'), ec.EXITS.get(event.get('reason') or kind, event.get('reason') or kind))
            elif kind.endswith('error'):
                add(event, f'{label} 실행기', '오류', event.get('symbol'), error_text(event))
        for r in ec.in_period(ec.cached_journal(root, engine), day):
            add(r, f'{label} 실행기', '체결', r['symbol'], f"{'매수' if r['side'] == 'BUY' else '매도'} {r['qty']:,}주 · {r['price']:,.0f}원 · {r['path']}"
                + (f" · {r['return_pct']:+.2f}%" if r['return_pct'] is not None else ''))
    previous = {}
    for event in ec.recent_events(runtime / 'live-follow.jsonl', 8000):
        account = event.get('account')
        if not account or not str(event.get('time', '')).startswith(day):
            continue
        signature = (event.get('action'), event.get('symbol'), event.get('reason'), event.get('execution'))
        if previous.get(account) == signature:
            continue
        previous[account] = signature
        label = account.replace('live-', '실계좌 ')
        read_failure = event.get('reason') in READ_FAILURES  # a failed broker read, not an order the guard refused
        add(event, label, '오류' if read_failure else '차단' if event.get('action') == 'BLOCK' else '실계좌 계획', event.get('symbol'),
            ('미러 실행기 · ' if read_failure else f"{L.ACTIONS.get(event.get('action'), event.get('action', ''))} · ") + L.reason(event.get('reason'))
            + (f" · {L.EXECUTIONS.get(event.get('execution'), event.get('execution'))}" if event.get('execution') else ''))
    longterm_timeline(runtime / 'vibe-longterm', day, add)
    from longterm_account_view import event_text
    for event in ec.recent_events(runtime / 'longterm-live.jsonl', 2000):  # already one line per change (longterm_live)
        if event.get('account') and str(event.get('time', '')).startswith(day):
            add(event, event['account'].replace('live-', '실계좌 ') + ' 장기투자', '차단' if event.get('action') == 'BLOCK' else '실계좌 계획',
                event.get('symbol'), event_text(event))
    rows.sort(key=lambda r: r['time'])
    return rows, waits


def longterm_timeline(root, day, add):
    """The long-term engine's decisions of `day` into the merged timeline (source '장기투자')."""
    path = Path(root) / 'longterm.sqlite3'
    if not path.exists():
        return
    import vibe_longterm as LT
    from sqlite_ro import ro_connect
    with ro_connect(path) as db:
        found = db.execute("SELECT time, kind, regime, orders, reason FROM decisions WHERE day = ? ORDER BY time, rowid", (day,)).fetchall()
    for stamp, kind, regime, orders, reason in found:
        if kind not in LT.DECISION_KINDS:
            continue
        orders = json.loads(orders) if orders else []
        text = (f"{LT.REGIMES[regime][0]} · " if kind == 'regime' and regime in LT.REGIMES else '') + (reason or '')[:120]
        if orders:
            text += f" · {len(orders)}건 (" + ', '.join(f"{LT.asset_name(o.get('key'))} "
                                                      f"{'매수' if o.get('side') == 'BUY' else '매도'} {o.get('qty', '')}" for o in orders[:3]) + ')'
        add({'time': stamp}, '장기투자', LT.DECISION_KINDS[kind], None, text)


WAIT_REASONS = {'volume_not_rising': '거래량 증가 없음', 'market_bars_unavailable': '분봉 조회 실패', 'trend_not_rising': '추세 상승 아님',
                'below_base': '기준가 아래', 'jev_wait': 'Jev 대기 의견', 'reentry_below_last_exit': '같은 날 재진입 제한',
                'losing_streak_cooldown': '연속 손실 쉬기', 'direction_not_confirmed': '지수 방향 불일치'}


READ_FAILURES = {'broker request failed', 'broker rate limit'}
ERRORS = {'v2_error': '실행기 주기 실패', 'v2_holding_decision_error': '보유 판단 실패(이번 주기 보유 유지)',
          'jev_error': 'Jev 비교 기록 실패', 'vibe_error': 'vibe 판단 실패'}
READS = {'quote unavailable': '시세', 'invalid bid': '매수호가', 'quote time unavailable or stale': '시세 시각',
         'paper signal bars unavailable': '분봉', 'paper broker page unavailable': '모의 잔고·주문 화면'}
ERROR_TYPES = {'IntegrityError': '기록 DB 동시 쓰기 충돌', 'StaleModelDecision': '모델 응답이 오래됨',
               'SignalOrSessionInvalid': '신호·장 시간 확인 실패', 'ValueError': '입력 확인 실패'}
DETAILS = {'stale holding inputs': '입력 데이터가 오래됨', 'stale holding bars': '분봉이 오래됨',
           'invalid Jev holding decision': 'Jev 응답 형식 오류', 'stale holding decision': '모델 응답이 오래됨'}


def error_text(event):
    """A runner error event in Korean: what failed and why; transient read failures say nothing was ordered."""
    head = ERRORS.get(event.get('event'), event.get('event'))
    detail, kind = event.get('detail'), event.get('error_type')
    if event.get('reason') == 'transient_read_failure':
        what = f"{READS.get(detail, detail)} 조회 실패" if detail else '증권사 조회 실패(상세 미기록)'
        return f'{head} · {what} · 주문 없음, 다음 주기 재시도'
    why = DETAILS.get(detail) or detail or ERROR_TYPES.get(kind) or kind or event.get('reason')
    return f'{head} · {why}' if why else head


def PATHS_LABEL(source):
    return ec.PATHS.get(source, source or '')


def render_records(runtime, records, names, settings, reason_label=str):
    """AI·실행 기록 종합: every engine's judgments, the runners' orders and fills and each live mirror's plans on one day."""
    import altair as alt
    import pandas as pd
    import streamlit as st
    runtime = Path(runtime)
    days = record_days(runtime, datetime.now().astimezone().strftime('%Y-%m-%d'))
    head, pick = st.columns([3, 1], vertical_alignment='bottom')
    head.subheader('AI·실행 기록 종합')
    day = pick.selectbox('기록 날짜', days, key='records_overview_day')
    rows, waits = timeline(runtime, day, names, reason_label)
    count = lambda source, kind: sum(1 for r in rows if r['출처'].startswith(source) and r['종류'] == kind)
    jev = [r['값'] for r in rows if r['출처'] == 'Jev' and r['종류'] == '판단' and r['값'] is not None]
    orders = sum(1 for r in rows if r['종류'].endswith('주문'))
    fills = sum(1 for r in rows if r['종류'] == '체결')
    live_plans = sum(1 for r in rows if r['종류'] == '실계좌 계획')
    errors = sum(1 for r in rows if r['종류'] in ('오류', '차단'))
    entry = settings.get('entry_confidence', 0.5)
    sc.row([{'label': 'Jev 판단', 'value': f'{len(jev)}회', 'tone': 'ai' if jev else 'off', 'icon': 'psychology',
             'sub': f"최고 신뢰도 {max(jev):.2f} (기준 {entry:.2f})" if jev else '기록 없음'},
            {'label': '진입 허용 · 대기', 'value': f"{count('', '진입 허용')} · {sum(waits.values())}", 'icon': 'rule',
             'tone': 'ok' if count('', '진입 허용') else 'off', 'sub': '실행기 진입 조건 통과 / 대기 회차'},
            {'label': '주문 · 체결', 'value': f'{orders} · {fills}', 'icon': 'swap_vert', 'tone': 'up' if fills else 'off',
             'sub': '엔진 실행기 기준'},
            {'label': '실계좌 계획 변경', 'value': f'{live_plans}회', 'icon': 'sync_alt', 'tone': 'warn' if live_plans else 'off',
             'sub': '같은 계획 반복 제외'},
            {'label': '오류 · 차단', 'value': f'{errors}건', 'icon': 'error', 'tone': 'alert' if errors else 'ok'}])
    import dialogs as dlg
    pick_rows = lambda test: [{'시각': r['time'][11:19], **{k: r[k] for k in ('출처', '종류', '종목', '내용')}}
                              for r in reversed(rows) if test(r)]
    note = f'{day} 기록 · 최신순.'
    dlg.buttons([('자세히 · 판단', 'rec_d_judged', dlg.table_dialog, (f'{day} 엔진 판단', pick_rows(lambda r: r['종류'] in ('판단', '보유 판단')), note)),
                 ('자세히 · 진입·대기', 'rec_d_waits', dlg.table_dialog,
                  (f'{day} 진입 허용과 대기 사유', [{'사유': k, '회차': n} for k, n in sorted(waits.items(), key=lambda kv: -kv[1])]
                   + [{'사유': f"진입 허용 · {r['종목']} {r['내용']}", '회차': 1} for r in rows if r['종류'] == '진입 허용'], note)),
                 ('자세히 · 주문·체결', 'rec_d_orders', dlg.table_dialog,
                  (f'{day} 주문·체결', pick_rows(lambda r: r['종류'].endswith('주문') or r['종류'] in ('체결', '청산 판단')), note)),
                 ('자세히 · 실계좌 계획', 'rec_d_live', dlg.table_dialog, (f'{day} 실계좌 계획 변경', pick_rows(lambda r: r['종류'] == '실계좌 계획'), note)),
                 ('자세히 · 오류·차단', 'rec_d_errors', dlg.table_dialog, (f'{day} 오류·차단', pick_rows(lambda r: r['종류'] in ('오류', '차단')), note))],
                columns=6)

    judged = [r for r in rows if r['종류'] == '판단' and r['값'] is not None]
    trades = [r for r in rows if r['종류'] == '체결']
    if judged:
        st.markdown('###### 판단 강도 추이와 체결')
        frame = pd.DataFrame(judged)
        frame['시각'] = pd.to_datetime(frame['time'], format='ISO8601')
        frame['엔진'] = 'Jev 후보 신뢰도'
        color = alt.Color('엔진:N', scale=alt.Scale(domain=['Jev 후보 신뢰도'], range=[ec.COLORS['Jev']]), legend=alt.Legend(orient='top', title=None))
        rules = pd.DataFrame([{'y': entry, '기준': f'Jev 진입 기준 {entry:.2f}'}])
        layers = [alt.Chart(frame).mark_line(point=True, strokeWidth=2).encode(
                      x=alt.X('시각:T', title=None, axis=alt.Axis(format='%H:%M')), y=alt.Y('값:Q', title='신뢰도 · 점수'),
                      color=color, tooltip=[alt.Tooltip('시각:T', format='%H:%M'), '엔진', '내용']),
                  alt.Chart(rules).mark_rule(strokeDash=[4, 4], color='#f59e0b').encode(y='y:Q', tooltip=['기준'])]
        if trades:
            marks = pd.DataFrame(trades)
            marks['시각'] = pd.to_datetime(marks['time'], format='ISO8601')
            marks['구분'] = marks['내용'].str.startswith('매수').map({True: '매수 ▲', False: '매도 ▼'})
            marks['y'] = -0.05
            layers.append(alt.Chart(marks).mark_point(filled=True, size=120).encode(
                x='시각:T', y='y:Q', shape=alt.Shape('구분:N', scale=alt.Scale(domain=['매수 ▲', '매도 ▼'], range=['triangle-up', 'triangle-down']),
                                                     legend=None),
                color=alt.Color('구분:N', scale=alt.Scale(domain=['매수 ▲', '매도 ▼'], range=['#ef4444', '#3b82f6']), legend=None),
                tooltip=['출처', '종목', '내용']))
        st.altair_chart(alt.layer(*layers).resolve_scale(color='independent').properties(height=260), width='stretch')
        st.caption('보라 선 = Jev 후보 신뢰도, 주황 점선 = 매수 기준, 아래 ▲▼ = 엔진 실행기 체결. '
                   '신뢰도·점수는 수익 확률이 아닙니다.')
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('###### 판단·실행 활동 (10분 단위)')
        from engine_roles import ledger_root
        heat = activity(runtime, day, ledger_root(runtime, 'jev'))
        if heat:
            frame = pd.DataFrame(heat)
            frame['구분'] = frame['구분'].map(lambda k: k.replace('live-', '실계좌 '))
            st.altair_chart(heatmap(frame), width='stretch')
        else:
            st.caption('이 날짜의 활동 기록이 없습니다.')
    with c2:
        st.markdown('###### 매수하지 않은 이유 (실행기 대기 회차)')
        if waits:
            frame = pd.DataFrame([{'사유': k, '회차': n} for k, n in sorted(waits.items(), key=lambda kv: -kv[1])[:8]])
            st.altair_chart(alt.Chart(frame).mark_bar(color='#64748b').encode(
                y=alt.Y('사유:N', sort='-x', title=None, axis=alt.Axis(labelLimit=260)), x=alt.X('회차:Q', title=None),
                tooltip=['사유', '회차']).properties(height=200), width='stretch')
        else:
            st.caption('대기 기록이 없습니다.')

    st.markdown('###### 통합 기록')
    sources = sorted({r['출처'] for r in rows})
    kinds = ['판단', '보유 판단', '진입 허용', '매수 주문', '매도 주문', '청산 판단', '체결', '실계좌 계획', '차단', '오류']
    f1, f2 = st.columns(2)
    picked_sources = f1.pills('출처', sources, selection_mode='multi', default=sources, key='records_overview_sources')
    picked_kinds = f2.pills('종류', [k for k in kinds if any(r['종류'] == k for r in rows)], selection_mode='multi',
                            default=[k for k in kinds if k not in ('판단',) and any(r['종류'] == k for r in rows)],
                            key='records_overview_kinds',
                            help='Jev·vibe 판단은 매분·15분마다 쌓여 많으므로 기본으로는 끄고, 주문·체결·계획 변화 위주로 보여 줍니다.')
    shown = [r for r in rows if r['출처'] in (picked_sources or []) and r['종류'] in (picked_kinds or [])]
    if shown:
        st.dataframe(pd.DataFrame([{'시각': r['time'][11:19], '출처': r['출처'], '종류': r['종류'], '종목': r['종목'] or '—', '내용': r['내용']}
                                   for r in reversed(shown)]), hide_index=True, width='stretch',
                     column_config={'내용': st.column_config.TextColumn(width='large')})
        st.caption(f'{len(shown)}건 · 최신순. 실계좌 계획은 바뀐 시점만, 판단 근거의 자세한 입력은 엔진을 골라서 봅니다.')
    else:
        st.caption('조건에 맞는 기록이 없습니다.')


from dialogs import alerts_dialog, holdings_dialog, realized_dialog, trades_dialog  # noqa: E402  (re-exported for callers)


def longterm_card(runtime):
    """Status card of the long-term engine (vibe): today's regime, NAV and plan, read-only."""
    try:
        import vibe_longterm as LT
        state = LT.read_state(Path(runtime) / 'vibe-longterm', ('plan', 'last_plan', 'nav_latest'))
        if not state:
            raise FileNotFoundError('no long-term record yet')
        plan, last = state.get('plan') or {}, state.get('last_plan') or {}
        regime = LT.REGIMES.get(plan.get('regime'), ('판정 전',))[0]
        nav = state.get('nav_latest') or {}
        sub = (f"순자산 {nav['actual']:,.0f}원" if nav.get('actual') else '순자산 기록 전') + \
            (f" · 적립 누계 {nav[LT.CONTRIBUTED]:,.0f}원" if nav.get(LT.CONTRIBUTED) else '') + (f" · {LT.why_label(last.get('why'))}" if last.get('why') else '')
        return {'label': '장기투자 국면', 'value': regime, 'tone': 'ai', 'icon': 'savings', 'sub': sub}
    except Exception:
        return {'label': '장기투자 국면', 'value': '기록 전', 'tone': 'off', 'icon': 'savings', 'sub': '평일 08:45 첫 판단'}

