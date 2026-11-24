"""Facts, blank report and checks for the long-term strategy report (docs/longterm-strategy-plan.md 5).

  --day D --status         what exists for D (plan, report files)
  --day D --data           the facts as JSON: regime, indicators, levels with past cases, account, stories, last report
  --day D --report --blank the report skeleton: every template section with its fact tables and (작성) where analysis goes
  --day D --check          checks R/strategy-D.md (template) and R/strategy-D.json (fields, advice levels inside the account)

R = ~/.vibe-trading/nhplug/vibe-longterm/reports. The report is written by the longterm-strategy-writer skill; this script
never orders anything and never changes the engine's rule levels.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from config_store import write_json, write_text  # the publisher may read the folder mid-write
import longterm_cases as C
import longterm_dca
import longterm_template as T
import longterm_watchlist
import vibe_longterm as L
import yeo_method

REPORTS = L.ROOT / 'reports'
SIDE = {'buy': '분할 매수', 'sell': '분할 매도', 'check': '점검'}
STATUS = {'waiting': '대기', 'near': '근접(3% 안)', 'hit': '닿음', 'filled': '집행됨'}
JSON_KEYS = ('summary', 'masters', 'scenarios', 'advice_levels', 'sources', 'ideas')  # 지난 계획 점검 is sections['지난 계획 점검']
TAX = 0.154      # 국내 상장 해외 ETF: gains taxed when sold
NOTICE = '참고 정보 · 주문 없음. 장기 엔진은 KRX 상장 ETF 6종만 운용한다. 공시를 확인한 종목만 싣고, 숫자는 코드 스크린(SEC 공시·Yahoo 시세) 값이다.'
# 거장별 관점 의견 (user approval 2026-11-18, 보완안 3): each master's OWN principle and representative portfolio, so the
# report reads today's market through the master and not only through our engine module of the same name. The writer adds
# the view (json master_views); the principle, portfolio and source here are fixed text with the master's own writing.
MASTER_VIEWS = {
    'bogle': {'name': '존 보글', 'modules': ['bogle_core'],
              'principle': '시장 전체를 낮은 비용으로 사서 오래 들고, 시장을 맞히려 하지 않는다. 비용·세금·잦은 매매가 수익을 갉아먹는다.',
              'portfolio': '전체 시장 인덱스 + 채권(채권 비중은 대략 나이만큼, 흔히 60/40) · 정해진 비율로 가끔 되돌림',
              'source': 'John C. Bogle, The Little Book of Common Sense Investing (2007)'},
    'dalio': {'name': '레이 달리오', 'modules': ['dalio_rebalance'],
              'principle': '성장·물가가 어느 쪽으로 갈지 모른다고 보고, 네 경제 국면에서 위험이 고르게 나뉘게 자산을 섞은 뒤 비율로 되돌린다.',
              'portfolio': '올웨더 30/40/15/7.5/7.5 (주식 30 · 장기 국채 40 · 중기 국채 15 · 금 7.5 · 원자재 7.5)',
              'source': 'Tony Robbins, Money: Master the Game (2014)의 달리오 인터뷰 · Bridgewater, The All Weather Story'},
    'buffett': {'name': '워런 버핏', 'modules': ['buffett_lowturn'],
                'principle': '좋은 기업(또는 시장 전체)을 적정한 값에 사서 거의 팔지 않는다. 남들이 두려워할 때 욕심을 내고, 시장 예측은 하지 않는다.',
                'portfolio': '90/10 (S&P500 인덱스 90 · 단기 미국 국채 10)',
                'source': 'Berkshire Hathaway 2013 주주서한'},
    'marks': {'name': '하워드 막스', 'modules': ['marks_pendulum'],
              'principle': '시장은 낙관과 비관 사이를 오가는 시계추다. 미래를 맞히지 않고, 지금 사이클의 어디쯤인지 보고 공격과 방어의 정도를 조절한다.',
              'portfolio': '고정 비율 없음 · 사이클 위치에 따라 위험 노출을 늘리고 줄임',
              'source': 'Howard Marks, The Most Important Thing (2011) · Mastering the Market Cycle (2018) · Oaktree 메모'},
    'lynch': {'name': '피터 린치', 'modules': ['lynch_story'],
              'principle': '아는 것에 투자하고, 산 이유(스토리)를 꾸준히 점검한다. 가격이 아니라 스토리가 깨질 때 판다.',
              'portfolio': '고정 비율 없음 · 저성장·대형 우량·고성장·경기순환·회생·자산주로 나눠 담음',
              'source': 'Peter Lynch, One Up on Wall Street (1989)'},
    'williams': {'name': '래리 윌리엄스', 'modules': ['williams_trend', 'williams_rebound'],
                 'principle': '가격 추세와 급락 뒤 단기 반등을 기계적 규칙으로 따른다. 손실은 짧게 자르고 의견보다 가격을 믿는다.',
                 'portfolio': '고정 비율 없음 · 추세 위에서는 들고, 아래에서는 줄임',
                 'source': 'Larry Williams, Long-Term Secrets to Short-Term Trading (1999)'},
    'yeo': {'name': '여운봉', 'modules': ['yeo_reservoir'],
            'principle': '평소에는 위험 70 : 안전 30을 지키고, 지수가 전고점 대비 크게 빠질 때만 저수지(대기 현금)를 단계로 쓴다. 저수지를 채우려고 산 것을 팔지 않는다.',
            'portfolio': '나스닥100(379810) 70 : 나스닥100미국채혼합50(0261H0) 30 · 저수지 미국 −20/−30/−40%(40/40/20%), 코스피 −30/−45/−55%(30/40/30%)',
            'source': '사용자 제공 규칙(docs/yeo-reservoir-method-plan.md 0·1절)'},
}
VIEW_ACTIONS = ('사기', '기다리기', '유지', '덜기', '리밸런싱')
VIEWS_FROM = '2026-11-19'  # reports ending on or after this day must carry master_views (the 11-18 one was published before)
PRICE_TALK = re.compile(r'목표\s*가|\$\s*\d|\d[\d,.]*\s*(달러|원|불)(?![가-힣])|\d[\d,.]*\s*(달러|원)\s*(까지|에서|이상|이하)')


def paths(day, reports=REPORTS):
    return Path(reports) / f'strategy-{day}.md', Path(reports) / f'strategy-{day}.json'


def previous_report(day, reports=REPORTS):
    """The latest report json before `day` ({} when there is none)."""
    same = T.KINDS[T.kind_of(day)]  # a daily report's previous one is a daily one (2026-10 sorts after 2026-10-30)
    older = sorted(p for p in Path(reports).glob('strategy-*.json')
                   if re.fullmatch(same, p.stem.removeprefix('strategy-')) and p.stem.removeprefix('strategy-') < day)
    try:
        return json.loads(older[-1].read_text(encoding='utf-8')) | {'day': older[-1].stem.removeprefix('strategy-')} if older else {}
    except (OSError, ValueError):
        return {}


def positions(closes):
    """{key: where each ETF stands on its own chart}: last close, vs 252-day high, vs 200/50-day lines."""
    out = {}
    for k, values in closes.items():
        if not values:
            continue
        hi, ma200, ma50 = max(values[-252:]), L.sma(values, 200), L.sma(values, 50)
        out[k] = {'close': values[-1], 'from_high': values[-1] / hi - 1, 'vs_ma200': values[-1] / ma200 - 1 if ma200 else None,
                  'vs_ma50': values[-1] / ma50 - 1 if ma50 else None, 'bars': len(values)}
    return out


def tp_rebalance(holdings, close, nav, target, tuning, sold_krw):
    """익절·리밸런싱 facts per asset: gain over the average cost, weight against the target and its band, the tax a full
    sale would pay, and this month's sell room left (buffett_lowturn). Pure: the report's table and the json read it."""
    band = tuning.get('band', 0.05)
    out = {}
    for k in L.ASSETS:
        qty, avg = holdings.get(k, (0, 0))
        price, want = close.get(k), target.get(k, 0)
        value = qty * price if qty and price else 0
        weight = round(value / nav, 4) if nav else 0
        if not qty and not want:
            continue
        lo, hi = round(max(0.0, want - band), 4), round(want + band, 4)
        out[k] = {'qty': qty, 'avg': avg, 'close': price, 'gain': round(price / avg - 1, 4) if qty and avg and price else None,
                  'weight': weight, 'target': want, 'band': [lo, hi], 'outside': not lo <= weight <= hi,
                  'tax_if_sold': round(max(0.0, value - qty * avg) * TAX) if qty else 0}
    return {'assets': out, 'sell_room': round(max(0.0, tuning.get('monthly_sell_share', L.MONTHLY_SELL_SHARE) * nav - (sold_krw or 0)))}


def ideas_facts(root):
    """The latest US idea screen (us_ideas, reference only): counts, the picks with their code reasons, the scorecard, 13F."""
    import us_ideas
    st = us_ideas.status(root)
    if not st.get('ready'):
        return {'ready': False}
    return {'ready': True, 'day': st['day'], 'passed': (st['runs'].get('weekly') or {}).get('passed') or {},
            'picks': [{k: p[k] for k in ('master', 'rank', 'ticker', 'name', 'reasons', 'etfs', 'new')} for p in st['picks']],
            'scorecard': st['scorecard'],
            'managers': [{'master': m['master'], 'firm': m['firm'], 'quarter': m.get('quarter'), 'changes': (m.get('changes') or [])[:8]}
                         for m in st['managers']]}


def yeo_accounts(root, plan, state):
    """{book key: where 여운봉 저수지 매매법 stands} for every book (the virtual one or a live account) using it: the index's
    drawdown from its all-time high, the next unused step, how far it is and what the rule does next. Read only."""
    import longterm_live
    settings = L.load_settings(root)
    ath = (plan.get('indicators') or {}).get('ath') or {}
    out = {}
    for key in settings.get('books') or {}:
        cfg = L.yeo_settings(settings, key)
        if cfg is None:
            continue
        book = state.get('book:actual') if key == 'actual' else longterm_live.load_state(Path(root).parent, key).get('book')
        ystate = (book or {}).get('yeo') or {}
        index, _, _, symbol, steps, _ = yeo_method.pairs(cfg)[0]
        used = (ystate.get(index) or {}).get('used') or []
        dd = (ath.get(index) or {}).get('dd')
        nxt = next(((n, level, use) for n, (level, use) in enumerate(steps, 1) if level not in used), None)
        if type(dd) not in (int, float):
            action = f'{symbol} 전고점 자료 없음: 오늘 단계 판단 안 함'
        elif nxt is None:
            action = '단계 모두 사용: 70:30 리밸런싱만(전고점 회복 시 재설정)'
        elif dd <= nxt[1]:
            action = f'{nxt[0]}단계 도달: 저수지 {nxt[2]:.0%} 매수 차례(70:30)'
        else:
            action = (f'다음 {nxt[0]}단계 {yeo_method.pct(nxt[1])}까지 {(dd - nxt[1]) * 100:.1f}%p 남음: '
                      + ('그 전에는 사지 않음' if not used else '보유분은 70:30 리밸런싱만, 새로 사지 않음'))
        out[key] = {'label': book_label(key), 'index': symbol, 'dd': dd, 'used': used,
                    'next_step': nxt[1] if nxt else None, 'next_action': action}
    return out


def dca_today(root, plan, state, now):
    """{book key: {label, today}}: when each book's 적립식 buys today, as far as it is known before the session
    (longterm_dca.today_text), for the books whose 적립식 or 적립 알림 is on. Read only."""
    import longterm_live
    settings, out = L.load_settings(root), {}
    for key, cfg in settings['dca'].items():
        if not cfg['on'] and not cfg.get('alert'):
            continue
        saved = state.get('dca:actual') if key == 'actual' else longterm_live.load_state(Path(root).parent, key).get('dca')
        want = L.dca_targets(L.today_want(plan), L.yeo_settings(settings, key))
        out[key] = {'label': book_label(key), 'waiting_krw': round((saved or {}).get('waiting_krw', 0)), 'last_buy': (saved or {}).get('last_buy'),
                    'today': longterm_dca.today_text(longterm_watchlist.for_dca(cfg, root), saved, now, plan.get('indicators') or {}, plan.get('regime'), want,
                                                         L.yeo_settings(settings, key) is not None)}
    return out


def book_label(key):
    """Never the account record's label (it may hold a masked account number): live-N → 실계좌 N."""
    return L.BOOK_LABELS.get(key) or (f"실계좌 {key.removeprefix('live-')}" if key.startswith('live-') else key)


def news_rows(root, day):
    """The long-term news headlines the engine read that morning (decisions kind 'news'), up to 18; [] when none."""
    path = Path(root) / 'longterm.sqlite3'
    if not day or not path.exists():
        return []
    from sqlite_ro import ro_connect
    with ro_connect(path) as db:
        row = db.execute("SELECT indicators FROM decisions WHERE kind='news' AND day=? ORDER BY time DESC LIMIT 1", (day,)).fetchone()
    try:
        rows = (json.loads(row[0]) or {}).get('rows') or [] if row and row[0] else []
    except ValueError:
        return []
    def at(text):
        try:
            return datetime.fromisoformat(text).astimezone(L.KST).strftime('%Y-%m-%d %H:%M')
        except (TypeError, ValueError):
            return None
    return [{'topic': r.get('topic'), 'title': r.get('title'), 'publisher': r.get('publisher'), 'url': r.get('link'), 'time': at(r.get('published'))}
            for r in rows[:18] if isinstance(r, dict)]  # longterm_news: six topics, three each


def held_now(root):
    """{code: shares} the virtual account holds."""
    last = {r['symbol']: r['after'] for r in L.journal_rows(root)}
    return {c: q for c, q in last.items() if q > 0}


def data(day, root=L.ROOT, reports=REPORTS, fetch=None, context=True):
    """Everything the report stands on, from records and read-only sources."""
    state = L.read_state(root)
    plan = state.get('plan') or {}
    evidence = C.evidence(root)
    levels = []
    for x in plan.get('levels') or []:
        item = evidence.get((x['asset'], x['case']))
        levels.append({**x, 'evidence': C.describe(item, evidence.get((x['asset'], 'baseline'))) if item else '과거 사례 없음(계좌 규칙)'})
    try:
        where = positions(L.krx_closes(fetch))
    except Exception as exc:
        where = {'error': f'{type(exc).__name__}'}
    last = state.get('last_plan') or {}
    book = state.get('book:actual') or {}
    outside = L.book_holdings(root, sleeve=True)  # held outside the engine's six: a 여운봉 book's own ETFs, the 관심목록 sleeve
    res = next((r['symbol'] for r in reversed(L.journal_rows(root)) if r['symbol'] in L.RESERVOIRS and r['after'] > 0
                and r['symbol'] in held_now(root)), L.KOFR)
    yeo_book = L.yeo_settings(L.load_settings(root), 'actual') is not None
    if yeo_book:  # the 여운봉 저수지 매매법 runs this book: no master module acts on its levels
        levels = [{**x, 'active': False} if x['kind'] == 'rule' else x for x in levels]
    facts = {'day': day, 'plan_day': plan.get('day'), 'plan_is_today': plan.get('day') == day,
             'regime': {'now': plan.get('regime'), 'raw': plan.get('raw'), **(state.get('regime') or {})},
             'indicators': plan.get('indicators') or {}, 'macro': plan.get('macro') or {}, 'mode': plan.get('mode'), 'active': plan.get('active') or [],
             'recommended': plan.get('recommended') or [], 'impacts': plan.get('impacts') or {}, 'weights': plan.get('weights') or {},
             'levels': levels, 'positions': where, 'stories': state.get('story') or {},
             'account': {'holdings': {k: list(v) for k, v in L.book_holdings(root).items()}, 'nav': state.get('nav_latest') or {},
                         'sleeve': {c: list(v) for c, v in outside.items() if c not in L.YEO_CODE_KEY}, 'reservoir': res, 'yeo_book': yeo_book,
                         'yeo': {L.YEO_CODE_KEY[c]: list(v) for c, v in outside.items() if c in L.YEO_CODE_KEY},
                         'sleeve_names': longterm_watchlist.load(root),
                         'last_plan': {k: last.get(k) for k in ('day', 'why', 'want', 'weights', 'orders', 'notes', 'execute')},
                         'pending': book.get('pending') or [], 'sold_this_month': book.get('sold') or {}},
             'previous_report': previous_report(day, reports)}
    close = {k: v['close'] for k, v in where.items() if isinstance(v, dict)}
    if res != L.KOFR:  # the reservoir ETF the book holds, not KOFR
        try:
            series = (fetch or L.yahoo)(f'{res}.KS')
            close['RES'] = series[max(series)]
        except Exception:  # noqa: BLE001 - no close: no gain is shown rather than a wrong one
            close.pop('RES', None)
    target = last.get('want') if last.get('day') == day and last.get('want') else plan.get('weights') or L.DEFAULT_SETTINGS['weights']
    nav = state.get('nav_latest') or {}  # the engine's ETFs weigh against the NAV without the 관심목록 sleeve
    facts['tp_rebalance'] = tp_rebalance(L.book_holdings(root), close, (nav.get('actual') or L.CAPITAL) - nav.get(L.SLEEVE, 0),
                                         target, plan.get('tuning') or L.DEFAULT_SETTINGS['tuning'], (book.get('sold') or {}).get('krw'))
    facts['master_principles'] = {k: {**v, 'engine': [{'module': m, 'label': f'{L.MODULES[m][0]} {L.MODULES[m][1]}', 'on': m in facts['active'],
                                                         'impact': '기본 비중' if m == 'bogle_core' else '; '.join(facts['impacts'].get(m) or []) or '없음'}
                                                        for m in v['modules']]} for k, v in MASTER_VIEWS.items()}
    facts['yeo_accounts'] = yeo_accounts(root, plan, state)
    facts['news'] = news_rows(root, plan.get('day'))
    facts['dca'] = dca_today(root, plan, state, datetime.combine(date.fromisoformat(plan['day']), datetime.min.time(), L.KST)) if plan.get('day') else {}
    try:
        facts['ideas'] = ideas_facts(root)
    except Exception as exc:  # reference only: the report goes on without it
        facts['ideas'] = {'ready': False, 'error': type(exc).__name__}
    if context:  # background only: never instructions
        for name, load in (('trading_journal', lambda: __import__('trading_journal').brief()),
                           ('premarket', lambda: __import__('premarket').engine_brief())):
            try:
                facts[name] = load()
            except Exception as exc:
                facts[name] = {'error': type(exc).__name__}
    return facts


def _pct(v, signed=True):
    return '—' if v is None else (f'{v * 100:+.1f}%' if signed else f'{v * 100:.1f}%')


def _table(head, rows):
    return '\n'.join(['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)] + ['| ' + ' | '.join(map(str, r)) + ' |' for r in rows])


FILLED = ('오늘의 결론', '거시 시황', '종목별 분석', '미국 종목 아이디어', '타점 지도', '계좌 계획', '익절·리밸런싱 의견',
          '지난 계획 점검', '위험·주의')  # free text sections


def report_md(facts, doc=None):
    """The report: the template's sections in order with the fact tables. Without `doc` it is the blank (작성 where the
    analysis goes); with `doc` (the analysis: `sections` texts + the json fields) every slot is filled from it."""
    doc = doc or {}
    p, ind = T.PLACEHOLDER, facts['indicators']
    text = lambda section: (doc.get('sections') or {}).get(section) or p
    res = facts['account'].get('reservoir')
    name, own = L.asset_name, lambda k: L.asset_name(k, res)  # the chart tables are KOFR's; what the book holds is its own reservoir ETF
    regime = L.REGIMES.get(facts['regime'].get('now'), ('판정 전',))[0]
    last = facts['account']['last_plan']
    # last_plan is written at 09:40; a morning report must not present the previous day's plan as today's
    planned = '오늘 계획' if last.get('day') == facts['day'] else f"최근 계획({last.get('day') or '없음'})"
    out = [T.title(facts['day']), '',
           f"> 자료 기준: 판단 {facts.get('plan_day') or '없음'}{'' if facts.get('plan_is_today') else ' (오늘 판단 아님)'} · 미국 종가 {ind.get('as_of', '—')} · "
           f"모드 {L.MODES.get(facts.get('mode'), '—')} · 가상 계좌(주문 없음은 dry-run)", '']
    out += ['## 1. 오늘의 결론', '', f"- 국면: **{regime}** · {planned}: {L.why_label(last.get('why'))}", '', text('오늘의 결론'), '']
    out += ['## 2. 거시 시황', '', _table(['지표', '값'], [
        ['나스닥100 252일 고점 대비', _pct(ind.get('dd'))], ['나스닥100 200일선 대비', _pct(ind.get('gap'))],
        ['200일선 20일 기울기', _pct(ind.get('slope'))], ['VIX', f"{ind.get('vix', 0):.1f}" if ind else '—'],
        ['원/달러', f"{ind.get('usdkrw', 0):,.0f}" if ind else '—'], ['이번 달 계절성(10년 평균, 참고)', _pct(ind.get('season_month_mean'))],
        *[[f'{name(k)} 원화 기준 252일 고점 대비(지수와 차이 = 환율·추적 차이)', _pct(facts['positions'][k]['from_high'])]
          for k in L.CORE if k in facts['positions'] and 'error' not in facts['positions']],
        ['확정 대기', f"{L.REGIMES[facts['regime']['pending']][0]} {facts['regime'].get('pending_days')}일째" if facts['regime'].get('pending') else '없음'],
        *[[f"{s['label']} (FRED {s['date']}{', 오래됨' if s.get('stale') else ''})",
           f"{s['value']:.2f}{s['unit']} · 1개월 {s['chg_1m']:+.2f} · 1년 {s['low_1y']:.2f}~{s['high_1y']:.2f}" if s.get('chg_1m') is not None
           else f"{s['value']:.2f}{s['unit']}"] for s in (facts.get('macro', {}).get('series') or {}).values()]]),
        '', '다가오는 미국 발표(30일, FRED·연준 일정): ' + (', '.join(f"{e['label']} {e['date']}" for e in facts.get('macro', {}).get('events') or [])
                                             or ('없음' if facts.get('macro', {}).get('as_of') else '자료 없음'))
        + (f" · 거시 자료 일부 없음: {', '.join(facts['macro']['errors'])}" if facts.get('macro', {}).get('errors') else ''),
        '', text('거시 시황'), '']
    out += ['## 3. 거장별 시각', '', _table(['거장', '운용', '추천', '오늘 영향(규칙)', '이 거장의 시각'], [
        [f'{m[0]} · {m[1]}', '켬' if k in facts['active'] else '끔', '추천' if k in facts['recommended'] else '',
         '기본 비중' if k == 'bogle_core' else ('; '.join(facts['impacts'].get(k) or []) or '없음'),
         ((doc.get('masters') or {}).get(k) or p).replace('|', '/').replace('\n', ' ')] for k, m in L.MODULES.items()]), '']
    out += views_md(facts, doc)
    stories = (facts['stories'] or {}).get('stories') or {}
    pos = facts['positions'] if 'error' not in facts['positions'] else {}
    out += ['## 4. 종목별 분석', '', _table(['종목', '종가', '252일 고점 대비', '200일선 대비', '50일선 대비', '스토리'], [
        [name(k), f"{v['close']:,.0f}", _pct(v['from_high']), _pct(v['vs_ma200']), _pct(v['vs_ma50']),
         L.STORY_LABELS.get((stories.get(k) or {}).get('status'), '—')] for k, v in pos.items()]), '', text('종목별 분석'), '']
    out += ideas_md(facts.get('ideas') or {}, doc.get('ideas') or [], text('미국 종목 아이디어'))
    out += ['## 6. 타점 지도', '', '규칙 = 엔진이 실제로 집행하는 선 · 제안 = 리포트 제안(주문 안 함). 크기는 계좌 한도에서 계산.', '',
            _table(['종목', '시간축', '거장', '구분', '가격', '현재가 대비', '크기', '조건', '규칙/제안', '상태', '과거 사례'], [
                [name(x['asset']), x['horizon'], L.MODULES[x['master']][0], SIDE[x['side']], f"{x['price']:,.0f}", _pct(x.get('distance')),
                 f"{x['size']['krw']:,}원", x['condition'], ('규칙' if x.get('active', True) else '규칙(이 장부 미적용)' if facts['account'].get('yeo_book') else '규칙(꺼짐)') if x['kind'] == 'rule' else '제안',
                 STATUS.get(x['status'], x['status']),
                 x['evidence']] for x in facts['levels']]), '', text('타점 지도'), '']
    if doc.get('advice_levels'):
        out += ['### 제안 타점 추가', '', _table(['종목', '시간축', '거장', '구분', '가격', '크기', '조건'], [
            [name(x['asset']), x['horizon'], L.MODULES[x['master']][0], SIDE[x['side']], f"{x['price']:,.0f}", f"{x['size']['krw']:,}원", x['condition']]
            for x in doc['advice_levels']]), '']
    acc = facts['account']
    want = last.get('want') if last.get('day') == facts['day'] else None  # the engine's target after stories and modules
    sleeve, held = acc['nav'].get(L.SLEEVE, 0), acc.get('sleeve') or {}  # 관심목록 holdings: the 적립식's, inside the NAV
    sleeve_rows = [['관심목록 슬리브(적립식 전용): ' + ', '.join((acc.get('sleeve_names') or {}).get(c, c) for c in held),
                    sum(q for q, _ in held.values()), '—', '—', '—',
                    _pct(sleeve / acc['nav']['actual'], False) if acc['nav'].get('actual') else '—']] if held else []
    out += ['## 7. 계좌 계획', '', _table(['종목', '보유 수량', '평균가', '설정 비중', '엔진 목표', '현재 비중'], [
        [own(k), (acc['holdings'].get(k) or [0])[0], f"{(acc['holdings'].get(k) or [0, 0])[1]:,.0f}", _pct(facts['weights'].get(k), False),
         _pct(want.get(k), False) if want else '09:40 계획 뒤', _pct((last.get('weights') or {}).get(k), False)] for k in L.ASSETS] + [[own(k), q, f'{avg:,.0f}', '—', _pct(want.get(k), False) if want else '09:40 계획 뒤', _pct((last.get('weights') or {}).get(k), False)]
                                     for k, (q, avg) in (acc.get('yeo') or {}).items()] + sleeve_rows), '',
        *([f"- 관심목록 슬리브 평가 {sleeve:,.0f}원: 순자산에 포함 · 엔진이 리밸런싱·매도하지 않음(적립식만 삼)"] if held else []),
        f"- 순자산 {acc['nav'].get('actual', L.CAPITAL):,.0f}원 · 대기 목표 {', '.join(name(k) for k in acc['pending']) or '없음'} · "
        f"이번 달 저수지 뺀 매도 {(acc['sold_this_month'] or {}).get('krw', 0):,.0f}원",
        *[f"- 적립식 · {d['label']}: {d['today']}" for d in (facts.get('dca') or {}).values()],
        f"- {planned} 주문: {len(last.get('orders') or [])}건 ({'가상 체결' if last.get('execute') else 'dry-run'})",
        '- 슬리브 예산: ' + ' · '.join(f"{label} {share * 100:.0f}%({share * acc['nav'].get('actual', L.CAPITAL):,.0f}원)"
                                   for label, share in sleeves(facts['weights'])), '', text('계좌 계획'), '']
    out += tp_md(facts.get('tp_rebalance') or {'assets': {}, 'sell_room': 0}, facts['active'], text('익절·리밸런싱 의견'), res, facts['account'].get('yeo_book', False))
    near = sorted(facts['levels'], key=lambda x: abs(x.get('distance') or 9))[:6]
    out += ['## 9. 시나리오 대응', '', '가까운 타점(현재가 대비): ' + '; '.join(
        f"{name(x['asset'])} {SIDE[x['side']]} {x['price']:,.0f}({_pct(x.get('distance'))})" for x in near), '',
        *([f"- {s['name']}: 트리거 {s['trigger']} → {s['action']}" for s in doc['scenarios']] if doc.get('scenarios') else
          ['- 상승 지속: ' + p, '- 횡보: ' + p, '- 급락(나스닥100 하루 −3%): ' + p, '- 조정 심화(고점 대비 −20%): ' + p, '- 과열: ' + p]), '']
    prev = facts['previous_report']
    period = T.kind_of(facts['day']) != 'day'  # weekly/monthly: the section is the period review (references/period.md)
    out += ['## 10. 지난 계획 점검', '', f"지난 리포트: {prev.get('day', '없음')}" + (f" — {prev.get('summary', '')}" if prev else ''), '',
            *(['**기간 점검** — 이 기간 일일 리포트들의 시나리오·제안 타점이 맞았는지(맞음·틀림·미발생 건수와 대표 사례)', ''] if period else []),
            text('지난 계획 점검'), '']
    out += ['## 11. 위험·주의', '', '- 과거 사례는 확률이 아니며 강세장 10년·미국 대용 자료·사례 수가 작다는 한계가 있다.',
            '- 지수 기준 타점은 오늘의 지수/ETF 비율로 옮긴 근사이며 환율 변화를 반영하지 않는다.', '', text('위험·주의'), '']
    sources = [f"- [{s['title']}]({s['url']})" + (f" · {s['time']}" if s.get('time') else '') for s in doc.get('sources') or []
               if isinstance(s, dict) and s.get('url')]
    out += ['## 12. 출처', '', *(sources or [f'- {p}']), '']
    return '\n'.join(out)


def views_md(facts, doc):
    """3절 '거장별 관점 의견': each master's own principle and portfolio (fixed text), our engine's module of that name, then
    the writer's view of today's market through that principle and what the master would do vs our engine/accounts.
    A filled report from before the section existed (no master_views) shows the principles only."""
    p, views = T.PLACEHOLDER, doc.get('master_views')
    blank = not doc or views is not None
    out = ['### 거장별 관점 의견', '', '> [!NOTE]', '> 참고 의견 · 주문 없음. 거장 자신의 원칙과 대표 포트폴리오로 오늘 시장을 읽은 것이다. '
           '엔진 모듈은 그 원칙을 우리 계좌 규칙으로 옮긴 것이라 다를 수 있다. 숫자는 이 리포트의 자료 값만 쓴다.', '']
    yeo = facts.get('yeo_accounts') or {}
    for k, m in (facts.get('master_principles') or MASTER_VIEWS).items():
        v = (views or {}).get(k) or {}
        engine = ' · '.join(f"{e['label']} {'켬' if e['on'] else '끔'}" + (f"({e['impact']})" if e['on'] and e['impact'] != '없음' else '')
                            for e in m.get('engine') or [])
        out += [f"#### {m['name']}", '', f"- 원칙: {m['principle']}", f"- 대표 포트폴리오: {m['portfolio']}",
                f"- 우리 엔진 모듈: {engine or '없음'}"]
        if k == 'yeo' and yeo:
            out += ["- 이 매매법을 쓰는 계좌: " + '; '.join(
                f"{a['label']} — " + (f"{a['index']} 전고점 대비 {_pct(a['dd'])} · " if a['dd'] is not None else '') + a['next_action']
                for a in yeo.values())]
        if blank:
            out += [f"- 지금 시장을 보면: {v.get('view') or p}",
                    f"- 그 거장이라면: **{v.get('action') or p}** · 엔진·계좌와: {v.get('vs_engine') or p}"]
        out += [f"- 출처: {m['source']}", '']
    return out


def views_required(key):
    """Whether the report `key` must carry master_views: it ends on or after VIEWS_FROM."""
    return report_end(key) >= VIEWS_FROM


def view_problems(views, required):
    if views is None:
        return ['master_views 항목이 없습니다(거장 7명의 관점 의견).'] if required else []
    if not isinstance(views, dict) or set(views) != set(MASTER_VIEWS):
        return [f"master_views에 거장 {len(MASTER_VIEWS)}명({', '.join(m['name'] for m in MASTER_VIEWS.values())})이 모두 있어야 합니다."]
    issues = []
    for k, v in views.items():
        name = MASTER_VIEWS[k]['name']
        v = v if isinstance(v, dict) else {}
        if not isinstance(v.get('view'), str) or len(v['view']) < 10:
            issues.append(f'master_views {name}: view(지금 시장을 그 원칙으로 본 의견)가 비었습니다.')
        if v.get('action') not in VIEW_ACTIONS:
            issues.append(f"master_views {name}: action은 {', '.join(VIEW_ACTIONS)} 중 하나여야 합니다.")
        vs = v.get('vs_engine')
        if not isinstance(vs, str) or not vs.startswith(('같음', '다름')) or len(vs) < 6:
            issues.append(f'master_views {name}: vs_engine은 "같음: …" 또는 "다름: …"으로 엔진·계좌와 비교한 근거를 씁니다.')
        if PRICE_TALK.search(' '.join(str(v.get(f, '')) for f in ('view', 'vs_engine'))):
            issues.append(f'master_views {name}: 가격·목표가·금액을 쓰지 않습니다(숫자는 자료 값만).')
    return issues


MASTER_NAMES = {'buffett': '워런 버핏', 'lynch': '피터 린치', 'fisher': '필립 피셔', 'tenbagger': '텐베거 후보'}


def ideas_md(ideas, cards, analysis):
    """5절: the screen's counts, then only the ideas the analysis checked against filings (never a bare screen name)."""
    out = ['## 5. 미국 종목 아이디어', '', '> [!IMPORTANT]', f'> {NOTICE}', '']
    if ideas.get('ready'):
        passed = ', '.join(f"{MASTER_NAMES.get(k, k)} {n}" for k, n in (ideas.get('passed') or {}).items())
        new = sum(1 for p in ideas.get('picks') or [] if p.get('new'))
        out += [f"- 코드 스크린 {ideas['day']}: 통과 {passed or '없음'} · 상위 후보 중 새로 든 것 {new}개 · 이름은 공시 확인 뒤 아래 표에만", '']
    else:
        out += ['- 코드 스크린 결과 없음(us_ideas 주간 실행 전)', '']
    reasons = {(p['master'], p['ticker']): ' · '.join(p['reasons']) for p in ideas.get('picks') or []}
    if cards:
        cell = lambda v: str(v).replace('|', '/').replace('\n', ' ')
        out += [_table(['종목', '거장', '코드 근거', '핵심 논리', '반증 가정', '레드라인', '재점검', '공시 확인'], [[cell(v) for v in (
            c['ticker'], MASTER_NAMES.get(c['master'], c['master']), reasons.get((c['master'], c['ticker']), '—'),
            c['thesis']['core'], ' / '.join(c['thesis']['falsifiers']), c['thesis']['redline'], c['thesis'].get('recheck', '—'),
            ' '.join(f"[{f.get('form', '공시')} {f.get('date', '')}]({f['url']})" for f in c['filings']))] for c in cards]), '']
    else:
        out += ['- 이번 리포트에 실은 종목 없음', '']
    if ideas.get('managers'):
        out += ['- 거장 13F(미국 상장 주식 롱만, 분기 말 뒤 45일 늦음): ' + '; '.join(
            f"{m['master']}({m['firm']}, {m.get('quarter') or '—'}) 변화 {len(m.get('changes') or [])}건" for m in ideas['managers']), '']
    return out + [analysis, '']


def tp_md(tp, active, analysis, res=None, yeo_book=False):
    """8절: the facts the take-profit/rebalancing opinion stands on; the engine's modules do the selling."""
    name = lambda k: L.asset_name(k, res)
    rows = [[name(k), f"{a['qty']:,}", f"{a['avg']:,.0f}" if a['qty'] else '—', f"{a['close']:,.0f}" if a['close'] else '—',
             _pct(a['gain']), _pct(a['weight'], False), _pct(a['target'], False), f"{a['band'][0] * 100:.0f}~{a['band'][1] * 100:.0f}%",
             '밖' if a['outside'] else '안', f"{a['tax_if_sold']:,}원"] for k, a in tp['assets'].items()]
    sellers = [f'{L.MODULES[m][0]} {L.MODULES[m][1]}' for m in ('dalio_rebalance', 'marks_pendulum', 'lynch_story', 'williams_trend', 'buffett_lowturn')
               if m in active]
    return ['## 8. 익절·리밸런싱 의견', '', _table(['종목', '보유', '평균가', '종가', '평가익', '현재 비중', '목표', '밴드', '밴드 안/밖', '전량 매도 시 세금(추정)'], rows), '',
            f"- 이번 달 남은 매도 한도(저수지 제외): {tp['sell_room']:,}원 · 파는 쪽 엔진 모듈(켜짐): {', '.join(sellers) or '없음'} · "
            '적립식은 팔지 않는다 · 세금은 국내 상장 해외 ETF 매매차익 15.4% 추정',
            *(['- 가상 계좌는 여운봉 저수지 매매법이라 위 밴드와 파는 모듈을 쓰지 않는다. 저수지 목표는 현금을 포함한 비중이라 저수지 ETF만의 비중과 다르다.'] if yeo_book else []), '', analysis, '']


def fill(day, doc, root=L.ROOT, reports=REPORTS, fetch=None):
    """Write R/strategy-<day>.md (the template filled from `doc`) and .json (`doc` itself) only when both pass; returns the
    problems. Nothing is written otherwise: the report publisher (report_pages) sends any report file that appears, so a
    draft must never land in the folder. A revision edits the analysis file and fills again."""
    md, js = paths(day, reports)
    text = report_md(data(day, root, reports, fetch, context=False), doc)
    found = T.check(text, day) + json_problems(doc, *account_basis(root, day), picks=picked(root), day=day)
    if found:
        return found
    md.parent.mkdir(parents=True, exist_ok=True)
    write_json(js, doc)  # with its sections: a revision refills from it
    write_text(md, text)  # last: the publisher watches the .md
    if doc.get('ideas') and Path(reports).resolve() == (Path(root) / 'reports').resolve():
        # the thesis cards live with the screen (the next lens step reads them); a report written elsewhere (an evaluation)
        # never touches the record
        import us_ideas
        db = us_ideas.connect(root)
        with db:
            db.executemany('INSERT OR REPLACE INTO theses VALUES (?,?,?,?)',
                           [(day, c['ticker'], c['master'], json.dumps(c, ensure_ascii=False)) for c in doc['ideas']])
    return check(day, root, reports)


def picked(root):
    """Every ticker the code screen has picked: a report idea must be one of them (its numbers came from code)."""
    path = Path(root) / 'ideas.sqlite3'
    if not path.exists():
        return set()
    from sqlite_ro import ro_connect
    with ro_connect(path) as db:
        return {r[0] for r in db.execute('SELECT DISTINCT ticker FROM picks')}


def report_end(key):
    """The last day a report key covers (a day, the Sunday of an ISO week, '-31' of a month: compared as text)."""
    kind = T.kind_of(key)
    return key if kind == 'day' else f'{key}-31' if kind == 'month' else date.fromisocalendar(int(key[:4]), int(key[6:]), 7).isoformat()


def account_basis(root, key):
    """(nav, configured weights) the json checks measure the report `key`'s suggested sizes against: the virtual
    account's NAV at its last close on or before the report's last day (CAPITAL before the first close)."""
    end = report_end(key)
    path = Path(root) / 'longterm.sqlite3'
    row = None
    if path.exists():
        from sqlite_ro import ro_connect
        with ro_connect(path) as db:
            row = db.execute("SELECT value FROM nav WHERE book='actual' AND day<=? ORDER BY day DESC LIMIT 1", (end,)).fetchone()
    return row[0] if row else L.CAPITAL, (L.read_state(root, ('plan',)).get('plan') or {}).get('weights')


def sleeves(weights):
    """[(label, target share)] for the core, the satellites together and the reservoir."""
    weights = weights or L.DEFAULT_SETTINGS['weights']
    return [('코어', sum(weights[k] for k in L.CORE)), ('위성 합계', sum(weights[k] for k in L.SATS)), ('저수지', weights['RES'])]


def json_problems(obj, nav, weights=None, picks=None, day=None):
    """Korean problems with the report json ([] when it passes). Suggested satellite buys must fit the satellites' target
    together (the configured weights, 25% by default). An idea card must name a code-screen pick (`picks`, when given),
    carry three falsifiers, a redline and the filings checked, and write no price, target or amount."""
    if not isinstance(obj, dict):
        return ['json이 객체가 아닙니다.']
    issues = [f'{k} 항목이 없습니다.' for k in JSON_KEYS if k not in obj]
    if not isinstance(obj.get('summary'), str) or len(obj.get('summary') or '') < 10:
        issues.append('summary가 비었습니다.')
    masters = obj.get('masters') if isinstance(obj.get('masters'), dict) else {}
    if set(masters) != set(L.MODULES) or not all(isinstance(v, str) and v for v in masters.values()):
        issues.append('masters에 거장 8명의 시각이 모두 있어야 합니다.')
    scenarios = obj.get('scenarios') if isinstance(obj.get('scenarios'), list) else []
    if len(scenarios) < 3 or not all(isinstance(s, dict) and {'name', 'trigger', 'action'} <= set(s) for s in scenarios):
        issues.append('scenarios는 name·trigger·action이 있는 3개 이상이어야 합니다.')
    advice = obj.get('advice_levels') if isinstance(obj.get('advice_levels'), list) else None
    if advice is None:
        issues.append('advice_levels는 목록이어야 합니다.')
    elif not all(isinstance(x, dict) for x in advice):
        issues.append('advice_levels 항목은 모두 타점 객체여야 합니다.')
    else:
        issues += L.level_problems(advice, nav)
        if any(x.get('kind') != 'advice' for x in advice if isinstance(x, dict)):
            issues.append('리포트는 제안(advice) 타점만 낼 수 있습니다. 규칙 타점은 엔진 값입니다.')
        cap = dict(sleeves(weights))['위성 합계'] * nav
        sat_buys = sum((x.get('size') or {}).get('krw', 0) for x in advice if isinstance(x, dict) and x.get('asset') in L.SATS and x.get('side') == 'buy')
        if sat_buys > cap + 1:
            issues.append(f'위성 제안 매수 합계 {sat_buys:,.0f}원이 위성 목표 {cap:,.0f}원을 넘습니다.')
    issues += idea_problems(obj.get('ideas'), picks)
    issues += view_problems(obj.get('master_views'), bool(day) and views_required(day))
    sources = obj.get('sources') if isinstance(obj.get('sources'), list) else []
    if not sources or not all(isinstance(s, dict) and s.get('url') and s.get('title') for s in sources):
        issues.append('sources는 title·url이 있는 출처 목록이어야 합니다.')
    return issues


def idea_problems(ideas, picks=None):
    if ideas is None:
        return []  # a missing key is reported with the other JSON_KEYS
    if not isinstance(ideas, list) or not all(isinstance(c, dict) for c in ideas):
        return ['ideas는 논리 카드 목록이어야 합니다.']
    issues = []
    for c in ideas:
        t, thesis = c.get('ticker'), c.get('thesis') if isinstance(c.get('thesis'), dict) else {}
        if c.get('master') not in MASTER_NAMES:
            issues.append(f'{t}: master는 {", ".join(MASTER_NAMES)} 중 하나여야 합니다.')
        if picks is not None and t not in picks:
            issues.append(f'{t}: 코드 스크린(us_ideas picks)에 없는 종목입니다. 숫자는 스크린에서 와야 합니다.')
        falsifiers = thesis.get('falsifiers')
        if not isinstance(thesis.get('core'), str) or not isinstance(falsifiers, list) or len(falsifiers) < 3:
            issues.append(f'{t}: 논리 카드에 핵심 논리와 반증 가정 3개가 필요합니다.')
        if not isinstance(thesis.get('redline'), str) or not thesis.get('redline'):
            issues.append(f'{t}: 레드라인이 필요합니다.')
        filings = c.get('filings') if isinstance(c.get('filings'), list) else []
        if not filings or not all(isinstance(f, dict) and 'sec.gov' in str(f.get('url', '')) for f in filings):
            issues.append(f'{t}: 확인한 SEC 공시(url)가 있어야 리포트에 실을 수 있습니다.')
        if not isinstance(c.get('lenses'), list) or len(c['lenses']) < 2:
            issues.append(f'{t}: 렌즈는 2개 이상(반대 렌즈 포함)이어야 합니다.')
        words = ' '.join(str(v) for v in [thesis.get('core'), thesis.get('redline'), c.get('verdict'), *(falsifiers or [])])
        if PRICE_TALK.search(words):
            issues.append(f'{t}: 가격·목표가·금액을 쓰지 않습니다(숫자는 코드 스크린 값만).')
    return issues


def check(day, root=L.ROOT, reports=REPORTS):
    md, js = paths(day, reports)
    problems = []
    stray = sorted(f.name for f in Path(reports).glob(f'strategy-{day}.*') if f not in (md, js))  # '.': 2026-11 ≠ 2026-11-17
    if stray:  # editor backups (sed -i …-E) or scratch files: the dashboard lists this folder as it is
        problems.append('리포트 폴더에 리포트가 아닌 파일이 있습니다(지우거나 /tmp로 옮기세요): ' + ', '.join(stray))
    try:
        problems += T.check(md.read_text(encoding='utf-8'), day)
    except OSError:
        problems.append(f'{md.name}이 없습니다.')
    try:
        problems += json_problems(json.loads(js.read_text(encoding='utf-8')), *account_basis(root, day), picks=picked(root), day=day)
    except (OSError, ValueError):
        problems.append(f'{js.name}이 없거나 json이 아닙니다.')
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--day', default=datetime.now(L.KST).date().isoformat())
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--status', action='store_true')
    group.add_argument('--data', action='store_true')
    group.add_argument('--report', action='store_true')
    group.add_argument('--check', action='store_true')
    group.add_argument('--fill', type=Path, metavar='FILE', help='write md+json from one analysis json (sections + report fields), then check')
    parser.add_argument('--blank', action='store_true', help='with --report: the skeleton to fill')
    parser.add_argument('--reports', type=Path, default=REPORTS, help='report folder (evaluations write elsewhere)')
    args = parser.parse_args()
    md, js = paths(args.day, args.reports)
    if args.status:
        plan = L.read_state().get('plan') or {}
        print(json.dumps({'day': args.day, 'plan_day': plan.get('day'), 'plan_is_today': plan.get('day') == args.day,
                          'trading_day': L.trading_day(datetime.fromisoformat(args.day).date()),
                          'written': md.exists() and js.exists(), 'report': str(md), 'json': str(js)}, ensure_ascii=False))
    elif args.data:
        print(json.dumps(data(args.day, reports=args.reports), ensure_ascii=False, indent=1, default=str))
    elif args.report:
        print(report_md(data(args.day, reports=args.reports, context=False)))
    else:
        problems = (fill(args.day, json.loads(args.fill.read_text(encoding='utf-8')), reports=args.reports) if args.fill
                    else check(args.day, reports=args.reports))
        print(json.dumps({'ok': not problems, 'problems': problems}, ensure_ascii=False, indent=1))
        sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
