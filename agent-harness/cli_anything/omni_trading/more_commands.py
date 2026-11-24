"""The rest of the commands: local views that need the engines' own functions, a few broker reads, and files the CLI writes."""
from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path

import click

from .core import reads
from .core.actions import live_record, live_service
from .core import settings as S
from .core.output import KST, Answer, Refused, command, source
from .utils.omni_backend import readonly
from .omni_trading_cli import account, code_of, compare, day_of, group, jev, journal, live, lt, market, nh, ops, premarket, report, research, run_script, status

ai = group('ai', 'AI 검색: 로컬 기록·내부 데이터(무료), 웹 검색·질문(유료)')
backup = group('backup', '백업')
template = group('template', '점검표')


def new_file(path):
    """A file the caller may write: never an existing one, never inside the runtime folder."""
    target = Path(path).expanduser().resolve()
    if target.exists():
        raise Refused('이미 있는 파일입니다. 다른 이름을 쓰세요.')
    if not target.parent.is_dir():
        raise Refused('폴더가 없습니다.')
    if (Path.home() / '.vibe-trading').resolve() in target.parents:
        raise Refused('런타임 폴더에는 쓰지 않습니다.')
    return target


def input_file(path, suffix):
    found = Path(path).expanduser().resolve()
    if not found.is_file() or found.suffix != suffix:
        raise Refused(f'{suffix} 파일이 아닙니다.', code=2)
    return found


# --- status, market -------------------------------------------------------------------------------------------------------

@status.command('refresh')
@command
def status_refresh():
    """실계좌 잔고를 한 번 조회해 기록(외부 조회, 주문 없음)"""
    from live_fills import record_balance
    from nh_live_broker import read_linked_balance
    root, read, out, warn = nh(), readonly(), [], []
    for key, record in reads.records(root).items():
        if record.get('kind') != 'live' or record.get('invalid'):
            continue
        try:
            balance, at = read_linked_balance(read, key, record)
            out.append({'account': key, 'recorded': bool(record_balance(root, key, balance, at, every=0))})
        except Exception as exc:  # noqa: BLE001 - a broker message may hold an account
            warn.append(f'{key}: 조회 실패({type(exc).__name__})')
    if warn and not out:
        raise Refused('실계좌 잔고를 조회하지 못했습니다. ' + ' · '.join(warn), code=6)
    return Answer({'accounts': out}, warnings=warn)


@market.command('refresh')
@command
def market_refresh():
    """시장 자료를 다시 모아 저장(화면의 갱신 버튼과 같음, 외부 조회 여러 번)"""
    import market_context
    nh()
    data = market_context.collect()
    market_context.write(data)
    return Answer({'time': data.get('time'), 'errors': data.get('errors') or []}, [source(market_context.FILE)])


@market.command('news')
@command
def market_news():
    """모든 실행기가 함께 쓰는 최근 시장 뉴스(저장된 것)"""
    return reads.file_answer(nh() / 'news-cache.json', ['fetched', 'rows', 'error'], 600)


@market.command('bars')
@click.argument('code')
@command
def market_bars(code):
    """NH 1분봉 30개(외부 조회 1회)"""
    nh()
    got = readonly().call_tool('call_api', {'operationId': 'krstockQuotePeriod', 'input': {
        'market_cd': 'KRX', 'iem_cd': code_of(code), 'gubun': '5', 'xtick': '1', 'today_cls_code': '1', 'view_main_yn': 'Y', 'array_cnt': '30'}})
    if got.get('status') != 'ok':
        raise Refused('NH 분봉을 받지 못했습니다.', code=6)
    keep = ('bsop_date', 'bsop_time', 'stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr', 'vol')
    return {'bars': [{k: r.get(k) for k in keep} for r in (got.get('data') or {}).get('Output_1') or [] if isinstance(r, dict)]}


@market.command('investors')
@click.argument('code')
@command
def market_investors(code):
    """종목의 외국인·기관·개인 순매수(직전 확정일·오늘 잠정, 외부 조회 1회)"""
    from watchlist import investor_days
    nh()
    found = investor_days(readonly(), code_of(code), reads.today())
    if found is None:
        raise Refused('NH 투자자 동향을 받지 못했습니다.', code=6)
    return found


@market.command('longterm')
@command
def market_longterm():
    """미국 장기 지표·거시 배경(장기 엔진의 아침 판단)"""
    L, state = engine_state()
    plan = state.get('plan') or {}
    if not plan.get('indicators'):
        raise Refused('아직 엔진 판단이 없습니다.', code=5)
    return Answer({'day': plan.get('day'), 'regime': state.get('regime'), 'indicators': plan['indicators'], 'temperature': L.temperature(plan['indicators'], plan.get('macro')),
                   'macro': plan.get('macro')}, [source(nh() / 'vibe-longterm/longterm.sqlite3')])


@market.command('ndx')
@click.option('--day', help='YYYY-MM-DD(기본 최신)')
@command
def market_ndx(day):
    """나스닥100 장중 관측(저장된 것)"""
    folder = nh() / 'vibe-longterm/ndx-intraday'
    found = sorted(folder.glob('*.json')) if not day else [folder / f'{day_of(day)}.json']
    if not found:
        raise Refused('관측 기록이 없습니다.', code=5)
    return reads.file_answer(found[-1])


# --- premarket, jev ---------------------------------------------------------------------------------------------------------

def review(name):
    path = nh() / 'shared-etf-paper/reviews' / name
    if not path.is_file():
        raise Refused(f'{name}이 없습니다.', code=5)
    return path


@premarket.command('read')
@click.option('--day')
@command
def premarket_read(day):
    """장전 상세 분석"""
    path = review(f'premarket-{day_of(day)}.md')
    return Answer({'text': path.read_text(encoding='utf-8')}, [source(path)])


@premarket.command('check')
@click.option('--day')
@command
def premarket_check(day):
    """장전 분석 템플릿 검사"""
    return run_script('premarket_template.py', '--check', str(review(f'premarket-{day_of(day)}.md')), ok=(0, 1))


@jev.command('strategy')
@command
def jev_strategy():
    """지금 시각·시황에서 예상되는 경로와 국면별 규칙(실제 판단 아님)"""
    import strategy_map as M
    from automation_status import processes
    state = M.state(nh() / 'shared-etf-paper', services=dict(processes()))
    nodes, text = M.active_route(state)
    return {'route': nodes, 'description': text, 'regimes': M.regime_rows(state)}


@jev.command('chart')
@click.argument('code')
@click.option('--day')
@command
def jev_chart(code, day):
    """그날 분봉과 체결(실행기 기록, 없으면 키움·Yahoo 조회)"""
    import trading_journal as J
    target = day_of(day)
    found = J.chart_data(target, code_of(code))
    fills = [r for r in reads.trades(nh(), target) if r.get('symbol') == code]
    return {'day': target, 'code': code, **found, 'fills': fills}


@jev.command('v2-samples')
@command
def jev_v2_samples():
    """낙폭과대 선별 v2 표본(사례집 재현, 체결 아님)"""
    import oversold_cases
    import oversold_replay
    import strategy_settings
    settings = strategy_settings.read_settings(nh() / 'shared-etf-paper')[0]
    return {'samples': oversold_replay.v2_samples(oversold_cases.load(), settings=settings)}


@jev.command('oversold')
@command
def jev_oversold():
    """낙폭과대 감시 종목과 오늘 기록, 선별 v2 재매수 감시"""
    root = nh()
    path = root / 'shared-etf-paper/events.jsonl'
    events = reads.tail(path, 30, reads.today(), lambda r: str(r.get('event', '')).startswith('oversold'))
    keep = ('time', 'event', 'symbol', 'reason', 'choice', 'confidence', 'drop_pct', 'passed', 'kospi', 'kosdaq', 'nasdaq_prev')
    return Answer({'universe': S.ALL['universe'].read(root), 'today': [{k: r[k] for k in keep if k in r} for r in events],
                   'v2_reentry': reads.read_json(root / 'shared-etf-paper/v2-reentry.json', {})}, [source(path)])


# --- accounts and live ---------------------------------------------------------------------------------------------------------

@account.command('quote')
@click.argument('key')
@click.argument('price', type=click.IntRange(1))
@click.argument('qty', type=click.IntRange(1))
@command
def account_quote(key, price, qty):
    """모의가 그 가격에 그 수량을 사면 이 실계좌가 살 수량(계산만)"""
    from nh_live_follow import follow_quote
    record = live_record(nh(), key)
    if record['engine'] != 'jev':
        raise Refused('Jev 트레이딩 실계좌만 계산합니다.')
    found = follow_quote(record, price, qty, reads.records(nh()).get('paper-shared'))
    return {name: {'shares': n, 'why': why} for name, (n, why) in found.items()} | {'now_uses': S.GROUPS['accounts'].read(nh())[f'{key}.rules.sizing']}


@account.command('checklist')
@click.argument('key')
@command
def account_checklist(key):
    """실주문이 나가기 위한 조건 4가지"""
    import longterm_account_view as V
    import os_services
    from src.live.halt import halt_flag_set
    record = live_record(nh(), key)
    service = live_service(key, record['engine'])
    try:
        args = os_services.registered_args(service, V.LAUNCH_AGENTS)
        armed = {'registered': args is not None, 'registered_for_real_orders': V.checklist(args, record)[0][1]}
    except os_services.Unknown:
        armed = {'registered': '확인 불가', 'registered_for_real_orders': '확인 불가'}
    return {'service': service, 'conditions': {**armed,
                                               'auto': record['auto'] is True, 'live_orders': record.get('live_orders') is True, 'halt_off': not halt_flag_set('nhplug')}}


@live.command('balance')
@click.argument('key')
@command
def live_balance(key):
    """실계좌 잔고와 보유(외부 조회, 주문 없음)"""
    from nh_live_broker import BALANCE_TOTALS, orderable_cash, read_linked_balance
    record = live_record(nh(), key)
    try:
        data, at = read_linked_balance(readonly(), key, record)
    except Exception as exc:  # noqa: BLE001 - a broker message may hold an account
        raise Refused(f'실계좌 잔고를 조회하지 못했습니다({type(exc).__name__}).', code=6) from None
    keep = ('iem_cd', 'iem_nm', 'rsdl_qty', 'phs_pr', 'now_pr', 'eal_amt', 'eal_pls_amt')
    return {'time': at.isoformat(timespec='seconds'), 'orderable_cash': orderable_cash(data['Output_0']), 'totals': {k: data['Output_0'].get(k) for k in BALANCE_TOTALS},
            'holdings': [{k: r.get(k) for k in keep if r.get(k) is not None} for r in data['Output_1'] if isinstance(r, dict) and r.get('rsdl_qty')]}


@live.command('longterm')
@click.argument('key')
@command
def live_longterm(key):
    """장기투자 실계좌의 오늘 계획·가상 운용"""
    import longterm_live
    root, record = nh(), live_record(nh(), key)
    if record['engine'] != 'longterm':
        raise Refused('장기투자 실계좌가 아닙니다.')
    state = longterm_live.load_state(root, key)
    path = root / 'vibe-longterm/longterm.sqlite3'
    nav = reads.rows(path, 'SELECT day, value, capital FROM account_nav WHERE account=? ORDER BY day DESC LIMIT 20', (key,))
    return Answer({'day': state.get('day'), 'orders': state.get('orders') or [], 'notes': (state.get('info') or {}).get('notes') or [], 'virtual_nav': nav},
                  [source(root / f'longterm-live/state-{key}.json'), source(path)])


# --- long term ------------------------------------------------------------------------------------------------------------------

def engine_state():
    import vibe_longterm as L
    return L, L.read_state(nh() / 'vibe-longterm', ('plan', 'story', 'book:actual', 'regime', 'dca:actual'))


@lt.command('recommend')
@command
def lt_recommend():
    """지금 국면의 거장 추천(바꾸지 않음)"""
    import longterm_dca as D
    L, state = engine_state()
    regime = (state.get('plan') or {}).get('regime')
    on = L.load_settings(nh() / 'vibe-longterm')['modules']
    rows = []
    for key, module in L.MODULES.items():
        grade, why, evidence = L.module_grade(key, regime)
        rows.append({'module': key, 'name': f'{module[0]} · {module[1]}', 'grade': grade, 'why': why, 'evidence': evidence, 'on': on.get(key)})
    return {'regime': regime, 'recommended': L.recommended_set(regime), 'modules': rows, 'dca_auto_profile': D.AUTO.get(regime)}


@lt.command('preview')
@click.argument('pairs', nargs=-1)
@command
def lt_preview(pairs):
    """오늘 목표 비중: 저장값과 바꾼 값(lt.modules.x=on lt.weights.NDX=0.25 …, 저장 안 함)"""
    import vibe_longterm_view as V
    L, state = engine_state()
    root, saved = nh(), L.load_settings(nh() / 'vibe-longterm')
    chosen = {'modules': dict(saved['modules']), 'weights': dict(saved['weights']), 'params': json.loads(json.dumps(saved['params']))}
    for pair in pairs:
        address, _, raw = pair.partition('=')
        g, key = S.split(address)
        if g.name != 'lt' or key.split('.')[0] not in chosen:
            raise Refused('lt.modules · lt.weights · lt.params만 미리 봅니다.')
        value = S.parse(S.spec_of(g, root, key), raw, address)
        part, *rest = key.split('.')
        target = chosen[part]
        for step in rest[:-1]:
            target = target[step]
        target[rest[-1]] = value
    if L.clean_params(chosen['params']) != chosen['params']:
        raise Refused('저수지·투매 2단계 낙폭은 1단계보다 깊어야 합니다.')
    if issues := L.weight_problems(chosen['weights']):
        raise Refused(' '.join(issues))
    plan = state.get('plan') or {}
    auto = saved['mode'] == 'auto' and saved['auto_live']
    used = list(plan.get('active') or []) if auto else [k for k, on in saved['modules'].items() if on]  # what the next plan uses, as on the screen
    rows = V.target_preview(plan, state, used, saved['weights'], chosen['modules'], chosen['weights'], saved['params'], chosen['params'])
    if rows is None:
        raise Refused('아직 엔진 판단이 없습니다.', code=5)
    label = L.book_strategy_label(saved, 'actual')
    warn = [f'가상 계좌는 지금 {label.split(" (")[0]}으로 운용합니다. 이 목표 비중은 쓰이지 않습니다.'] if label.startswith('여운봉') else []
    return Answer({'day': plan.get('day'), 'strategy': label, 'targets': rows}, warnings=warn)


@lt.command('dca-preview')
@click.argument('book', default='actual')
@click.argument('pairs', nargs=-1)
@command
def lt_dca_preview(book, pairs):
    """적립식 적용값과 이번 회차 예시(amount_krw=200000 …, 저장·주문 안 함)"""
    import longterm_dca as D
    import longterm_dca_effective as E
    L, state = engine_state()
    root = nh()
    g = S.GROUPS['dca']
    if f'{book}.on' not in g.specs(root):
        raise Refused(f'장부가 아닙니다: {book}')
    saved = L.load_settings(root / 'vibe-longterm')
    cfg = D.clean(saved['dca'].get(book))
    for pair in pairs:
        key, _, raw = pair.partition('=')
        key = key.removeprefix(f'dca.{book}.')
        value = S.parse(S.spec_of(g, root, f'{book}.{key}'), raw, key)
        node, *rest = key.split('.')
        if node == 'tune':
            target = cfg['tune'].setdefault('mult', {}) if rest[0] == 'mult' else cfg['tune']
            target[rest[-1]] = value
        else:
            cfg[node] = value
    cfg = D.clean(cfg)
    regime, ind, want, (prices, src) = E.example_inputs(root / 'vibe-longterm', book)
    yeo = L.yeo_settings(saved, book) is not None
    value = (L.read_state(root / 'vibe-longterm', ('last_plan',)).get('last_plan') or {}).get('nav') if book == 'actual' else reads.records(root)[book]['budget']['capital_krw']
    got = D.effective(cfg, regime, ind, want, prices, cash=(value or 0) if yeo else None, state=state.get('dca:actual') if book == 'actual' else None, yeo=yeo)  # as the screen
    day = D.next_day(cfg, datetime.now(KST).date()).isoformat()
    return {'book': book, 'on': cfg['on'], **({} if cfg['on'] else {'off_note': f'적립식이 꺼져 있어 사지 않습니다. 켜면 다음 확인일은 {day}입니다. 아래 값은 켰을 때의 예시입니다.'}),
            'regime': regime, 'prices_from': src, 'fields': [{'label': f['label'], 'value': f['value']} for f in got['fields']],
            'example': [{k: r[k] for k in ('name', 'weight', 'price', 'krw', 'qty', 'carry', 'excluded') if k in r} for r in got['rows']], 'notes': got['notes'],
            'next_day': day if cfg['on'] else None}


@lt.command('recipes')
@command
def lt_recipes():
    """레시피 비교 가상 장부(1억 원, 시작일·지금·손익)"""
    path = nh() / 'vibe-longterm/longterm.sqlite3'
    found = reads.rows(path, "SELECT account, min(day) AS start, max(day) AS last, count(*) AS days, "
                             "(SELECT value FROM account_nav b WHERE b.account=a.account ORDER BY day DESC LIMIT 1) AS value, capital "
                             "FROM account_nav a WHERE account LIKE 'recipe:%' GROUP BY account ORDER BY value DESC")
    return Answer({'recipes': [{**r, 'account': r['account'].removeprefix('recipe:'), 'pnl_pct': round((r['value'] / r['capital'] - 1) * 100, 2)} for r in found]}, [source(path)])


@lt.command('yeo')
@command
def lt_yeo():
    """여운봉 매매법 장부의 낙폭·다음 단계·할 일"""
    import longterm_report
    L, state = engine_state()
    return longterm_report.yeo_accounts(nh() / 'vibe-longterm', state.get('plan') or {}, state)


@lt.command('chart')
@click.argument('key')
@click.option('--days', default=120, type=click.IntRange(5, 500))
@command
def lt_chart(key, days):
    """엔진 ETF 일봉 종가와 타점(외부 조회 1회)"""
    L, state = engine_state()
    if key not in L.ASSETS:
        raise Refused(f"{' · '.join(L.ASSETS)} 중 하나입니다.", code=2)
    try:
        series = L._series(L.yahoo(f'{L.ASSETS[key][0]}.KS', 2))[-days:]
    except Exception as exc:  # noqa: BLE001
        raise Refused(f'시세를 받지 못했습니다({type(exc).__name__}).', code=6) from None
    levels = [x for x in (state.get('plan') or {}).get('levels') or [] if x.get('asset') == key]
    return {'asset': key, 'name': L.ASSETS[key][1], 'closes': [[str(d), c] for d, c in series], 'levels': levels}


# --- journal, report, compare, research -------------------------------------------------------------------------------------------

@journal.command('period')
@click.argument('key')
@click.option('--parts', help='쉼표로 고른 항목')
@command
def journal_period(key, parts):
    """기간 일지(2026-11 · 2026-Q4 · 2026-H2 · 2026)"""
    import trading_journal as J
    nh()
    found = J.load_period(key)
    if not found:
        raise Refused('해당 기간 일지가 없습니다.', code=5)
    want = [p.strip() for p in parts.split(',')] if parts else list(found)
    return {k: found[k] for k in want if k in found}


@journal.command('review')
@command
def journal_review():
    """장 마감 복기(최근 5회, 저장된 것)"""
    import research_store
    path = nh() / 'shared-etf-paper/market-research.sqlite3'
    return Answer({'reviews': research_store.history('daily_review', 5, path)}, [source(path)])


@journal.command('check')
@click.argument('key')
@command
def journal_check(key):
    """분석 일지 템플릿 검사(날짜 또는 기간 키)"""
    if not re.fullmatch(r'\d{4}(-\d{2}-\d{2}|-\d{2}|-Q[1-4]|-H[12])?', key):
        raise Refused('YYYY-MM-DD 또는 기간 키로 적으세요.', code=2)
    return run_script('journal_template.py', '--check', str(review(f'journal-{key}.md')), ok=(0, 1))


@journal.command('rebuild')
@click.option('--day', required=True)
@command
def journal_rebuild(day):
    """저장된 하루 일지의 자료를 다시 모음(외부 조회, 일지 DB에 씀)"""
    import trading_journal as J
    target = day_of(day)
    if target > reads.today() or date.fromisoformat(target).weekday() >= 5:
        raise Refused('주말이나 미래 날짜의 일지는 만들지 않습니다.')
    if not J.load(target):
        raise Refused('저장된 일지가 없습니다.', code=5)
    nh()
    return run_script('trading_journal.py', '--rebuild', '--day', target)


@report.command('preview')
@click.argument('kind')
@click.argument('markdown')
@click.option('--out', required=True, help='새로 만들 폴더')
@command
def report_preview(kind, markdown, out):
    """리포트 HTML 미리보기(발행·발송·기록 없음)"""
    nh()
    folder = new_file(out)
    return run_script('report_pages.py', '--preview', kind, str(input_file(markdown, '.md')), '--out', str(folder), as_text=True, ok=(0, 1))


@report.command('check-page')
@click.argument('page')
@command
def report_check_page(page):
    """만든 HTML 페이지 검사"""
    return run_script('report_pages.py', '--check', str(input_file(page, '.html')), as_text=True, ok=(0, 1))


@compare.command('export')
@click.option('--out', required=True, help='새 CSV 파일')
@click.option('--start')
@click.option('--end')
@command
def compare_export(out, start, end):
    """성과 표를 CSV로 내보내기"""
    import strategy_compare
    rows, target = strategy_compare.rows(nh(), start=start, end=end), new_file(out)
    if not rows:
        raise Refused('자료가 없습니다.', code=5)
    with target.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[k for k in rows[0] if k != 'bench'], extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    return {'file': str(target), 'rows': len(rows)}


@research.command('cases', context_settings={'ignore_unknown_options': True})  # -6 is a fall, not an option
@click.argument('code')
@click.argument('drop', type=float)
@command
def research_cases(code, drop):
    """비슷한 과거 급락 사례(사례집)"""
    return run_script('oversold_cases.py', '--similar', code_of(code), str(drop))


@research.command('ledger')
@command
def research_ledger():
    """거래 원장 이벤트 수"""
    path = nh() / 'shared-etf-paper/trading-ledger.sqlite3'
    return Answer({'events': reads.rows(path, 'SELECT event, count(*) AS n, max(time) AS last FROM events_v2 GROUP BY event ORDER BY n DESC')}, [source(path)])


# --- ai ----------------------------------------------------------------------------------------------------------------------------

@ai.command('local')
@click.argument('question')
@command
def ai_local(question):
    """내부 데이터 질문(성과·체결·보유·상태, 실계좌 보유는 증권사 조회)"""
    import liner_local_data as D
    query = D.resolve(question)
    if query is None:
        raise Refused('내부 데이터로 답할 수 있는 질문이 아닙니다. 계좌와 기간을 함께 적으세요.')
    return {'answer': D.answer(query, nh(), datetime.now(KST))}


@ai.command('topics')
@click.argument('word', required=False)
@command
def ai_topics(word):
    """추천 질문과 서비스 가이드 찾기"""
    import liner_topics as T
    if word:
        return {'guides': T.find_guides(word), 'questions': [q for qs in T.groups().values() for q in qs if word in q]}
    return {'groups': {k: list(v) for k, v in T.groups().items()}}


@ai.command('history')
@click.option('--find', default='')
@click.option('--archived', is_flag=True)
@click.option('--limit', default=20, type=click.IntRange(1, 200))
@command
def ai_history(find, archived, limit):
    """지난 AI 검색 대화 목록"""
    import liner_history as H
    nh()
    return {'talks': H.threads(find, limit, archived)}


@ai.command('talk')
@click.argument('number', type=int)
@command
def ai_talk(number):
    """지난 대화 하나"""
    import liner_history as H
    nh()
    found = H.load(number)
    if not found:
        raise Refused('해당 대화가 없습니다.', code=5)
    return found


# --- ops, backup, template -----------------------------------------------------------------------------------------------------------

@ops.command('api-bench')
@click.argument('need', required=False)
@command
def ops_api_bench(need):
    """저장된 API 실측 결과(새로 재지 않음)"""
    folder = nh() / 'api-bench'
    found = sorted(folder.glob('*.json'))
    if not need:
        return {'results': [p.name for p in found]}
    match = [p for p in found if p.name.startswith(need)]
    if not match:
        raise Refused('해당 실측 결과가 없습니다.', code=5)
    return reads.file_answer(match[-1])


def backups():
    import state_sync
    return state_sync, state_sync.STATE / 'backups'


@backup.command('list')
@command
def backup_list():
    """백업 목록"""
    nh()
    sync, folder = backups()
    return {'backups': [{'name': p.name, 'mb': round(p.stat().st_size / 1e6, 1), 'time': datetime.fromtimestamp(p.stat().st_mtime, KST).isoformat(timespec='minutes')}
                        for p in sync.archives(folder)]}


@backup.command('verify')
@click.argument('name')
@command
def backup_verify(name):
    """백업 하나를 검사"""
    nh()
    sync, folder = backups()
    path = next((p for p in sync.archives(folder) if p.name == name), None)
    if path is None:
        raise Refused('해당 백업이 없습니다. `omni backup list`로 확인하세요.', code=5)
    problems = sync.verify(path)
    return Answer({'name': name, 'ok': not problems, 'problems': problems}, code=1 if problems else 0)


@backup.command('create')
@command
def backup_create():
    """지금 백업(저장소 state/backups, 최근 14개만 보관)"""
    root = nh()
    sync, folder = backups()
    newest = next(iter(sync.archives(folder)), None)
    if newest and datetime.now().timestamp() - newest.stat().st_mtime < 3600:
        raise Refused('한 시간 안에 만든 백업이 있습니다. 새로 만들 때마다 가장 오래된 백업이 지워집니다.')
    path, manifest = sync.export_archive(root.parent, folder)
    return {'name': path.name, 'files': len(manifest['files']), 'mb': round(path.stat().st_size / 1e6, 1)}


@template.command('startup')
@click.option('--blank', is_flag=True, help='빈 점검표')
@click.option('--check', 'path', help='검사할 .md')
@command
def template_startup(blank, path):
    """창업 준비 점검표"""
    if bool(path) == blank:
        raise Refused('--blank와 --check 중 하나를 쓰세요.', code=2)
    return run_script('startup_template.py', '--blank', as_text=True) if blank else \
        run_script('startup_template.py', '--check', str(input_file(path, '.md')), as_text=True, ok=(0, 1))


from . import action_commands  # noqa: E402,F401 - registers the approved requests
