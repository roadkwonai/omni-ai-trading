"""Why each long-term order was placed (user request 2026-11-18, docs/audit/longterm-audit-2026-11-18.md 5): one evidence
bundle per order of the virtual book (engine trade and 적립식, executed or planned), stored in longterm.sqlite3 `evidence`
and linked to the virtual order number. Eight parts, each marked used (it changed the order) or reference only:

  reason   the code's trade reason (initial deployment and its weekly share, monthly band, module change, 적립식 rule)
  regime   the regime call: indicators, criteria, confirmation
  masters  each master module's change to this ETF's target (base → final); masters that were off and what they would do
  macro    FRED rates, credit and the release calendar (reference: the rules never read it)
  story    the theme story (used only for a satellite while lynch_story is on)
  news     what news reached the engine (none directly; the story check's own sources)
  trading  order and fill price, weight before and target, cash, the day's buy cap
  research the level map, past cases, 13F and US ideas (reference only)

Recording only: nothing here changes a target or an order. reconstruct() rebuilds the bundles of a day from the decision
log and state, marked 재구성."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import longterm_dca as D
import vibe_longterm as L

SCHEMA = '''CREATE TABLE IF NOT EXISTS evidence (time TEXT, day TEXT, book TEXT, kind TEXT, key TEXT, code TEXT, side TEXT,
  qty INTEGER, price REAL, number INTEGER, filled INTEGER, fill_price REAL, why TEXT, masters TEXT, reconstructed INTEGER,
  summary TEXT, bundle TEXT)'''
PART_TITLES = {'reason': '매매 사유', 'regime': '국면 판정', 'masters': '거장 판단', 'macro': '거시 배경', 'story': '테마 스토리',
               'news': '뉴스·시황', 'trading': '트레이딩 데이터', 'research': '수집·분석 데이터'}
KIND_LABELS = {'order': '가상 주문', 'plan': '계획(dry-run)', 'dca': '적립식'}
pct = lambda v: f'{v:.1%}'  # noqa: E731


def ensure(db):
    with db:
        db.execute(SCHEMA)


def part(key, used, text, **more):
    return {'title': PART_TITLES[key], 'used': used, 'text': text, **more}


def master_steps(plan, stories, book_state, day):
    """{'base': {key: weight}, 'steps': [{module, label, active, before, after, notes, note}]}: the active masters applied one by
    one in the engine's order (the last step is the day's target) and the masters that were off with what they would do."""
    ind, regime, weights = plan['indicators'], plan['regime'], plan.get('weights')
    active = [m for m in L.MODULES if m in plan['active']]
    params = plan.get('params')
    base = L.targets(ind, [], regime, book_state, stories, day, weights, params)[0]  # no module: everything waits in the reservoir
    steps, chosen, before = [], [], base
    for m in L.MODULES:
        label = f'{L.MODULES[m][0]} · {L.MODULES[m][1]}'
        if m in active:
            chosen.append(m)
            after, _, notes = L.targets(ind, chosen, regime, book_state, stories, day, weights, params)
            own = [t for mod, t in notes if mod == m]
            steps.append({'module': m, 'label': label, 'active': True, 'before': before, 'after': after, 'notes': own,
                          'note': '; '.join(own) or '오늘 바꾼 것 없음'})
            before = after
        else:
            would = (plan.get('impacts') or {}).get(m) or (['기준 비중을 들고 감(강력 추천)'] if m == 'bogle_core' else [])
            steps.append({'module': m, 'label': label, 'active': False, 'before': None, 'after': None, 'notes': would,
                          'note': '꺼짐 — ' + ('켜졌다면: ' + '; '.join(would) if would else '오늘은 켜졌어도 영향 없음')})
    return {'base': base, 'steps': steps}


def context(db, day):
    """The day's plan, regime state, story and the story check's inputs, from state and the decision log."""
    st = L.get_state(db)
    row = db.execute("SELECT targets, reason, time FROM decisions WHERE day=? AND kind='story' ORDER BY rowid DESC LIMIT 1", (day,)).fetchone()
    seen = json.loads(row[0]) if row and row[0] else {}
    news = db.execute("SELECT indicators FROM decisions WHERE day=? AND kind='news' ORDER BY rowid DESC LIMIT 1", (day,)).fetchone()
    return {'plan': st.get('plan') or {}, 'regime_state': st.get('regime') or {}, 'story': st.get('story') or {},
            'story_seen': seen, 'story_time': row[2] if row else None, 'news': json.loads(news[0]) if news and news[0] else None}


def _fills(root, numbers):
    db = Path(root) / 'virtual-account.sqlite3'
    if not numbers or not db.exists():
        return {}
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        marks = ','.join('?' * len(numbers))
        return {n: (f, p) for n, f, p in con.execute(f'SELECT number, filled, fill_price FROM orders WHERE number IN ({marks})', list(numbers))}
    finally:
        con.close()


def common_parts(ctx, key):
    plan, rs, story, seen = ctx['plan'], ctx['regime_state'], ctx['story'], ctx['story_seen']
    ind, regime = plan.get('indicators') or {}, plan.get('regime')
    label, rule = (L.REGIMES[regime][0], L.REGIMES[regime][1]) if regime in L.REGIMES else (regime, '')
    need = L.CONFIRM_DAYS.get(regime, (plan.get('tuning') or {}).get('confirm_days', 10))
    out = {'regime': part('regime', True,
                          f"{label} 국면({rule}): 나스닥100 낙폭 {ind.get('dd', 0):.1%}, 200일선 대비 {ind.get('gap', 0):+.1%}, VIX {ind.get('vix', 0):.1f}"
                          f" · {rs.get('since') or '—'}부터 확정(확정에 {need}거래일)",
                          source='Yahoo Finance 일봉', at=ind.get('as_of'),
                          how='모듈 계산(과열·투매 조건)과 추천 조합에 쓰임' + (' · 수동 모드라 활성 거장은 사용자가 정함' if plan.get('mode') == 'manual' else ''))}
    macro = plan.get('macro') or {}
    series = [f"{s['label']} {s['value']}{s.get('unit', '')}({s.get('date')})" for s in (macro.get('series') or {}).values() if s.get('value') is not None]
    events = [f"{e.get('name') or e.get('label')} {e.get('date')}" for e in (macro.get('events') or macro.get('calendar') or [])][:6]
    out['macro'] = part('macro', False, ' · '.join(series) or '거시 자료 없음', items=events, source=macro.get('source', 'FRED · 연준'),
                        at=macro.get('as_of'), how='참고만 함: 주문 계산은 거시 값을 읽지 않음(스토리 판단과 리포트에 전달)')
    stories = story.get('stories') or {}
    lynch = 'lynch_story' in (plan.get('active') or [])
    if key in L.SATS and key in stories:
        s = stories[key]
        effect = {'broken': '목표 0', 'watch': '목표 절반'}.get(s['status'], '그대로') + (f", 기울기 {s['tilt']:+d}" if s.get('tilt') else '')
        out['story'] = part('story', lynch, f"{L.STORY_LABELS.get(s['status'], s['status'])}: {s['reason']}",
                            effect=effect if lynch else f'피터 린치 모듈이 꺼져 있어 쓰이지 않음({effect}였을 것)',
                            view=story.get('market_view', ''), source=f"LLM 스토리 점검 [{story.get('source', '?')}]", at=story.get('time'))
    else:
        out['story'] = part('story', False, '코어·저수지 ETF는 테마 스토리 대상이 아님' if key not in L.SATS else '스토리 판단 없음',
                            view=story.get('market_view', ''), source='LLM 스토리 점검', at=story.get('time'))
    sources, inputs = seen.get('sources') or [], seen.get('inputs') or {}
    given = [n for n, k in (('장전 브리프', 'premarket'), ('매매일지 요약', 'trading_journal'), ('거시', 'macro')) if inputs.get(k)]
    news = ctx.get('news') or {}
    import longterm_news
    heads = [f"[{longterm_news.TOPIC_LABELS.get(r.get('topic'), r.get('topic'))}] {r['title']} · {r.get('publisher', '')} · "
             f"{str(r.get('published', ''))[:16].replace('T', ' ')}" + (f" · {r['link']}" if r.get('link') else '') for r in news.get('rows') or []]
    text = (f"엔진이 받은 장기 관점 뉴스 {len(heads)}건(연준·미국 거시·반도체·반도체 정책·지정학·양자·우주, 아침 수집)" if heads else
            f"뉴스 입력 없음({news['error']})" if news.get('error') else '뉴스 입력 없음: 그날 장기 관점 뉴스 기록이 없음')
    out['news'] = part('news', False, text + (f" · 스토리 점검 LLM이 근거로 남긴 자료 {len(sources)}건" if sources else ''),
                       items=heads + [f'스토리 점검 근거: {x}' for x in sources], given=given,
                       source='판단 기록 news(Google·Bing·네이버 순환) · story.targets', at=news.get('as_of') or ctx.get('story_time'),
                       how='참고만 함: 주문 계산은 뉴스를 직접 읽지 않음' + (' · 피터 린치 스토리 판단을 거쳐 이 위성 목표에 간접 반영'
                                                                  if key in L.SATS and lynch else ''))
    levels = [lv for lv in (plan.get('levels') or []) if lv.get('asset') == key]  # levels key the ETF as 'asset'
    out['research'] = part('research', False, f'타점 지도 {len(levels)}개(엔진 규칙 가격 표시·제안)' if levels else '이 ETF의 타점 지도 없음',
                           items=[f"{lv.get('master', '')} {lv.get('side', '')} {lv.get('price', '')} {lv.get('condition', '')}".strip()
                                  for lv in levels][:6],
                           how='참고만 함: 과거 사례 DB·13F·미국 종목 아이디어는 엔진 입력이 아님', source='plan.levels · cases.sqlite3 · ideas.sqlite3')
    return out


def unfilled(row):
    """' (미체결 취소)' for a sent virtual order that did not fill, else ''."""
    return ' (미체결 취소)' if row.get('number') and not row.get('filled') else ''


def order_bundle(ctx, row):
    """The eight parts and the one-sentence summary for one engine order (plan or order)."""
    key, plan = row['key'], ctx['plan']
    steps = ctx['steps']
    final = ctx['want'].get(key, 0)
    masters = [{'module': s['module'], 'label': s['label'], 'active': s['active'],
                'before': (s['before'] or {}).get(key), 'after': (s['after'] or {}).get(key), 'note': s['note']} for s in steps['steps']]
    moved = [m for m in masters if m['active'] and m['before'] is not None and abs(m['after'] - m['before']) > 1e-9]
    tuning = plan.get('tuning') or {}
    reason_notes = [t for m, t in ctx['notes']]
    why_text = L.why_label(ctx['why'])
    if ctx['why'] == 'initial_deployment':
        detail = (next((t for m, t in ctx['notes'] if m == 'deploy'), None)
                  or f"하루 매수 상한 순자산의 {tuning.get('daily_buy_share', L.DAILY_BUY_SHARE):.0%}")
        why_text += f' — {detail}'
    parts = {'reason': part('reason', True, why_text, items=reason_notes, code=ctx['why'], how='주문 계산에 쓰임')}
    common = common_parts(ctx, key)
    weight = ctx['weights'].get(key, 0)
    trade_to = (ctx['target'] or {}).get(key)
    cap = tuning.get('daily_buy_share', L.DAILY_BUY_SHARE) * ctx['nav']
    bought = sum(v for v in ctx['planned'].values() if v > 0)
    fill = (f" · 체결 {row.get('filled', 0):,}주 @ {row['fill_price']:,.0f}원" if row.get('fill_price') else unfilled(row).replace(' (', ' · ').rstrip(')'))
    if str(ctx['why']).startswith('yeo'):  # 여운봉 저수지 매매법: its own steps and 70 : 30, no master module, no regime
        ath = ((plan.get('indicators') or {}).get('ath') or {}).get('ndx') or {}
        parts['regime'] = {**common['regime'], 'used': False, 'how': '참고만 함: 여운봉 저수지 매매법은 국면을 읽지 않고 전고점 대비 낙폭만 씀'}
        parts['masters'] = part('masters', True, '여운봉 저수지 매매법: 거장 모듈을 쓰지 않음 — ^NDX 전고점 대비 '
                                + (f"{ath['dd']:.1%}" if type(ath.get('dd')) in (int, float) else '—') + ' · 단계 −20/−30/−40%에서 저수지 40/40/20%를 '
                                '위험(379810) 70 : 안전(0261H0) 30으로, 보유 뒤 매월·분기 첫 확인일과 5%p 이탈 즉시 70:30 리밸런싱', steps=[],
                                how='주문 계산에 쓰임(전고점 대비 낙폭·저수지 금액·70:30)', source='엔진 지표 ath(Yahoo 10년 일봉 최고가)')
    else:
        parts.update(regime=common['regime'], masters=part(
        'masters', True, f"모듈 전 {pct(steps['base'].get(key, 0))} → 최종 {pct(final)}"
        + (' · ' + ', '.join(f"{m['label'].split(' · ')[0]} {pct(m['before'])}→{pct(m['after'])}" for m in moved) if moved else ' · 거장 모듈이 바꾼 것 없음'),
        steps=masters, base=steps['base'].get(key, 0), final=final, active=plan.get('active'), recommended=plan.get('recommended'), mode=plan.get('mode'),
        how='활성 거장만 주문 계산에 쓰임'))
    parts.update(macro=common['macro'], story=common['story'], news=common['news'])
    parts['trading'] = part('trading', True,
                            f"{'매수' if row['side'] == 'BUY' else '매도'} {row['qty']:,}주 × 주문가 {row['price']:,}원{fill}"
                            f" · 비중 {pct(weight)} → 목표 {pct(final)}" + (f"(오늘 {pct(trade_to)}까지)" if trade_to is not None and abs(trade_to - final) > 1e-9 else ''),
                            nav=ctx['nav'], cash=ctx['cash'], planned_krw=round(ctx['planned'].get(key, 0)),
                            buy_cap=round(cap), buy_cap_used=round(bought), source='가상 브로커(NH 호가) · 엔진 계산', how='주문 계산에 쓰임')
    parts['research'] = common['research']
    name = L.asset_name(key)
    label = L.REGIMES.get(plan.get('regime'), (plan.get('regime'),))[0]
    summary = (f"{name} {'매수' if row['side'] == 'BUY' else '매도'} {row['qty']:,}주{unfilled(row)}: {why_text}. {label} 국면, "
               f"목표 비중 {pct(final)}(지금 {pct(weight)})" + (f", 비중을 바꾼 거장: {', '.join(m['label'].split(' · ')[0] for m in moved)}" if moved else '')
               + '. 거시·뉴스·아이디어는 참고만 함.')
    return {'summary': summary, 'parts': parts, 'masters': [m['module'] for m in moved]}


def dca_bundle(ctx, row, cfg, notes, regime):
    key = row['key']
    cfg = D.clean(cfg)  # v3 (2026-11-18): 언제 · 얼마나 by the master or by me
    p = D.rule(cfg, regime)
    who = f"{D.STYLES[cfg['style']]} · {p['label']}({p['title']})"
    yeo = ctx.get('yeo', False)
    text = f"언제 {D.BY[cfg['when_by']]}: {D.when_text(cfg, p)} · 얼마나 {D.BY[cfg['size_by']]}: {D.size_text(cfg, p, regime, value=ctx.get('value'), yeo=yeo)}"
    if cfg.get('tune'):  # 내 조정 laid over the master's numbers (user 2026-11-18)
        text += f" · 내 조정: {D.tune_text(cfg['tune'])}"
    parts = {'reason': part('reason', True, f'적립식 {who}: {text}', items=list(notes),
                            profile=cfg.get('profile'), style=cfg.get('style'), when_by=cfg['when_by'], size_by=cfg['size_by'],
                            amount=cfg.get('amount_krw'), how='주문 계산에 쓰임(적립식은 사기만 함)')}
    common = common_parts(ctx, key)
    want = ctx['want'].get(key, 0)
    reads = cfg['when_by'] == 'master' or cfg['size_by'] == 'master'
    parts['regime'] = {**common['regime'], 'used': reads,
                       'how': '거장의 국면 배수·사는 날에 쓰임' if reads else '참고만 함: 내가 정한 날·금액은 국면을 읽지 않음'}
    basis = '여운봉 저수지 매매법의 위험:안전 짝' if yeo else '엔진 목표 비중'
    parts['masters'] = part('masters', True, f'배분: 그날 {basis} {pct(want)}대로 나눔', how=f'{basis}으로 배분(계좌 전략을 따름)')
    parts.update(macro=common['macro'], story=common['story'], news=common['news'])
    fill = f" · 체결 {row.get('filled', 0):,}주 @ {row['fill_price']:,.0f}원" if row.get('fill_price') else unfilled(row).replace(' (', ' · ').rstrip(')')
    parts['trading'] = part('trading', True, f"매수 {row['qty']:,}주 × 주문가 {row['price']:,}원{fill}", amount=cfg.get('amount_krw'),
                            source='가상 브로커(NH 호가)', how='주문 계산에 쓰임')
    parts['research'] = common['research']
    name = L.asset_name(key)
    return {'summary': f"{name} 적립 매수 {row['qty']:,}주{unfilled(row)}: {who} 규칙, {basis} {pct(want)}로 배분. 거시·뉴스는 참고만 함.",
            'parts': parts, 'masters': [cfg['profile']]}


def _insert(db, root, day, kind, rows, bundles, why, reconstructed, now, note=''):
    ensure(db)
    fills = _fills(root, [r.get('number') for r in rows if r.get('number')])
    stamp = (now or datetime.now(L.KST)).isoformat(timespec='seconds')
    with db:
        for r, b in zip(rows, bundles):
            filled, fill_price = fills.get(r.get('number'), (r.get('filled'), None))
            db.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (stamp, day, 'actual', kind, r['key'], r.get('code'), r['side'], r['qty'], r['price'], r.get('number'), filled,
                        fill_price, why, json.dumps(b['masters'], ensure_ascii=False), int(reconstructed), b['summary'],
                        json.dumps({'parts': b['parts'], 'note': note}, ensure_ascii=False, default=str)))


def _with_fills(root, rows):
    fills = _fills(root, [r.get('number') for r in rows if r.get('number')])
    return [{**r, 'filled': fills.get(r.get('number'), (r.get('filled'), None))[0], 'fill_price': fills.get(r.get('number'), (None, None))[1]}
            for r in rows]


def record_trade(db, root, day, kind, plan, book_state, stories, want, target, why, notes, planned, weights, nav, cash, sent, now=None,
                 reconstructed=False, note=''):
    """Bundles for the engine's orders of the day (kind 'order' executed or 'plan' dry)."""
    if not sent:
        return 0
    ctx = {**context(db, day), 'plan': plan, 'want': want, 'target': target, 'why': why, 'notes': notes, 'planned': planned,
           'weights': weights, 'nav': nav, 'cash': cash}
    yeo = str(why).startswith('yeo')  # 여운봉 저수지 매매법: no master module took part
    ctx['steps'] = {'base': {}, 'steps': []} if yeo else master_steps(plan, stories, book_state, day)
    rows = _with_fills(root, sent)
    _insert(db, root, day, kind, rows, [order_bundle(ctx, r) for r in rows], why, reconstructed, now, note)
    return len(rows)


def record_dca(db, root, day, plan, cfg, want, notes, sent, now=None, reconstructed=False, note='', yeo=False, value=None):
    if not sent:
        return 0
    ctx = {**context(db, day), 'plan': plan, 'want': want, 'yeo': yeo, 'value': value}
    rows = _with_fills(root, sent)
    _insert(db, root, day, 'dca', rows, [dca_bundle(ctx, r, cfg, notes, plan.get('regime')) for r in rows], 'dca', reconstructed, now, note)
    return len(rows)


def reconstruct(root, day):
    """Bundles for the orders of `day` that have none, rebuilt from the decision log and state (marked 재구성): the plan of
    that day must still be the state's plan. Returns how many were written."""
    db = L.connect(root)
    ensure(db)
    st = L.get_state(db)
    plan, last = st.get('plan') or {}, st.get('last_plan') or {}
    if plan.get('day') != day:
        return 0
    done = {k for (k,) in db.execute('SELECT DISTINCT kind FROM evidence WHERE day=?', (day,))}
    note = '재구성: 그날 남은 계획·판단 기록·상태로 다시 만든 근거입니다(주문 당시 그대로 저장한 것이 아님). 거장 단계는 시작 상태를 비운 채 계산했습니다.'
    written = 0
    stories = (st.get('story') or {}).get('stories') or {}
    for kind, orders, reason in db.execute("SELECT kind, orders, reason FROM decisions WHERE day=? AND kind IN ('order','plan','dca') ORDER BY rowid DESC", (day,)).fetchall():  # the latest per kind: an executed 적립식 over its dry plan
        if kind in done or not orders:
            continue
        sent = json.loads(orders)
        if not sent:
            continue
        done.add(kind)
        if kind == 'dca':
            cfg = (L.load_settings(root)['dca'].get('actual')) or D.clean({})
            written += record_dca(db, root, day, plan, cfg, last.get('want') or plan.get('weights') or {}, [reason or ''], sent,
                                  reconstructed=True, note=note)
        else:
            notes = [tuple(n) for n in last.get('notes') or []]
            written += record_trade(db, root, day, kind, plan, {}, stories, last.get('want') or {}, last.get('trade_to'), last.get('why'),
                                    notes, {o['key']: o['qty'] * o['price'] for o in sent}, last.get('weights') or {}, last.get('nav') or L.CAPITAL,
                                    None, sent, reconstructed=True, note=note)
    return written


def rows(root, start=None, end=None):
    """Evidence rows (newest first) for the views, read-only."""
    db = Path(root) / 'longterm.sqlite3'
    if not db.exists():
        return []
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='evidence'").fetchone():
            return []
        sql, args = 'SELECT rowid AS id, * FROM evidence', []
        if start:
            sql, args = sql + ' WHERE day>=?' + (' AND day<=?' if end else ''), [start] + ([end] if end else [])
        return [dict(r) for r in con.execute(sql + ' ORDER BY day DESC, rowid', args)]
    finally:
        con.close()


if __name__ == '__main__':
    import sys
    print(json.dumps({'reconstructed': reconstruct(L.ROOT, sys.argv[1])}, ensure_ascii=False))
