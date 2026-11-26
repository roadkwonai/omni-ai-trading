"""omni: read Omni AI Trading's records, plan and apply its settings. It never places, changes or cancels an order."""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sys
from datetime import date, datetime
from pathlib import Path

import click

from .core import approval, reads
from .core import settings as S
from .core.output import KST, Answer, OmniError, Refused, command, source
from .utils import omni_backend as backend

DAY = re.compile(r'\d{4}-\d{2}-\d{2}')
PERIOD = re.compile(r'\d{4}-(\d{2}-\d{2}|W\d{2}|\d{2})')
KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,40}')


def nh():
    return backend.nhplug()


def agent():
    return click.get_current_context().find_root().obj['agent']


def code_of(value):
    backend.load()
    from etf_universe import is_code
    if not is_code(value):
        raise Refused('종목코드는 6자리입니다.', code=2)
    return value


def day_of(value):
    if not value:
        return reads.today()
    try:
        if DAY.fullmatch(value) and date.fromisoformat(value):
            return value
    except ValueError:
        pass
    raise Refused(f'날짜는 YYYY-MM-DD입니다: {value}', code=2)


class Root(click.Group):
    def main(self, args=None, **kw):
        if hasattr(sys.stdout, 'reconfigure'):  # Korean Windows prints cp949 otherwise
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        logging.disable(logging.WARNING)  # screen modules warn about running outside Streamlit
        args = list(sys.argv[1:] if args is None else args)
        here = [i for i, a in enumerate(args) if a == '--json' and (i == 0 or args[i - 1] not in ('--reason', '--name', '--code'))]
        if here:  # agents put it last
            args = ['--json'] + [a for i, a in enumerate(args) if i not in here]
        if not here or kw.get('standalone_mode') is False:
            return super().main(args, **kw)
        try:  # with --json even a wrong option answers in the envelope
            done = super().main(args, **{**kw, 'standalone_mode': False})
        except click.ClickException as exc:
            body = {'type': 'usage' if isinstance(exc, click.UsageError) else 'failed', 'message': exc.format_message(), 'next': ['omni guide']}
            click.echo(json.dumps({'ok': False, 'command': '', 'code': exc.exit_code, 'time': datetime.now(KST).isoformat(timespec='seconds'), 'error': body,
                                   'data': {}, 'source': [], 'warnings': []}, ensure_ascii=False))
            sys.exit(exc.exit_code)
        except click.Abort:
            sys.exit(1)
        sys.exit(done if isinstance(done, int) else 0)


@click.group(cls=Root, invoke_without_command=True, context_settings={'help_option_names': ['-h', '--help']})
@click.option('--json', 'as_json', is_flag=True, help='한 줄 JSON으로 출력')
@click.option('--agent', default=lambda: os.environ.get('OMNI_AGENT', 'cli'), help='이력에 남길 이름')
@click.version_option('0.1.0', prog_name='omni')
@click.pass_context
def cli(ctx, as_json, agent):
    """Omni AI Trading의 기록을 조회하고 설정 변경을 계획하며, 사용자가 승인한 요청을 남깁니다. 직접 주문하지 않습니다."""
    above = ctx.obj if isinstance(ctx.obj, dict) else {}  # a repl line keeps what the repl was started with
    ctx.obj = {'json': as_json or above.get('json', False), 'agent': re.sub(r'^approved', 'agent', re.sub(r'[^\w.-]', '', agent)[:40]) or 'cli'}
    if ctx.obj['agent'] == 'cli' and above.get('agent'):
        ctx.obj['agent'] = above['agent']
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


@cli.command()
@click.pass_context
def repl(ctx):
    """명령을 이어서 입력하는 대화형 모드(exit로 종료)"""
    if not sys.stdin.isatty():
        click.echo(ctx.parent.get_help() if ctx.parent else ctx.get_help())
        return
    click.echo('omni · help 명령 목록 · exit 종료')
    while True:
        try:
            line = input('omni> ').strip()
        except (EOFError, KeyboardInterrupt):
            return
        if line in ('exit', 'quit'):
            return
        if line:
            args = ['--help'] if line == 'help' else shlex.split(line)
            try:
                cli.main(args=args, prog_name='omni', standalone_mode=False, obj=dict(ctx.find_root().obj))
            except click.ClickException as exc:
                exc.show()
            except click.exceptions.Exit:
                pass


def group(name, text):
    @cli.group(name=name, help=text)
    def made():
        pass
    return made


def run_script(name, *args, as_text=False, ok=(0,), timeout=180):
    """A read-only script of the checkout; its JSON or its text."""
    code, out, err = backend.script(name, *args, timeout=timeout)
    if code not in ok:
        if not out.strip() and len(err.strip().splitlines()) == 1:  # the script's own words (SystemExit), not a crash
            raise Refused(err.strip()[:300])
        raise OmniError(f'{name}이 실패했습니다(종료 {code}).', 1, {'output': (out or err)[-1500:]})
    data = {'text': out.strip()}
    if not as_text:
        try:
            data = json.loads(out)
        except ValueError:
            pass
    data = {'rows': data} if isinstance(data, list) else data
    return Answer(data, code=1) if code else data  # a check that found problems is not ok


# --- status ------------------------------------------------------------------------------------------------------------

@cli.group(invoke_without_command=True)
@click.pass_context
def status(ctx):
    """전체 상태(엔진·서비스·실주문 스위치·HALT)"""
    if ctx.invoked_subcommand is None:
        ctx.invoke(status_all)


@status.command('all')
@command
def status_all():
    """엔진·서비스·실주문 스위치·HALT"""
    return reads.status(nh())


@status.command('alerts')
@command
def status_alerts():
    """주의 항목"""
    root = nh()
    return Answer({'alerts': reads.alerts(root)}, [source(root / n) for n in ('shared-etf-paper/events.jsonl', 'live-follow.jsonl', 'longterm-live.jsonl', LT_DB)])


@status.command('balance')
@command
def status_balance():
    """계좌별·전체 잔고와 원금 대비 손익(실행기 기록)"""
    files = [nh() / 'shared-etf-paper/account-snapshots.jsonl', *sorted(nh().glob('live-balance-*.jsonl')), nh() / LT_DB]
    return Answer({'unit': '원, 수익률은 %', 'balances': reads.balance(nh())}, [source(f) for f in files])


@status.command('realized')
@command
def status_realized():
    """오늘 실현손익"""
    root = nh()
    return Answer({'realized': reads.realized(root)}, [source(root / n) for n in ('shared-etf-paper/orders.sqlite3', 'live-fills.sqlite3')])


@status.command('trades')
@click.option('--day')
@command
def status_trades(day):
    """그날 모든 계좌의 거래"""
    found = reads.trades(nh(), day_of(day))
    warn = ['실계좌의 "가상" 행은 실체결이 없는 날의 추정입니다(실제 체결 아님).'] if any(str(r.get('source', '')).startswith('가상') for r in found) else []
    return Answer({'trades': found}, warnings=warn)


@status.command('timeline')
@click.option('--day')
@click.option('--limit', default=50, type=click.IntRange(1, 500))
@command
def status_timeline(day, limit):
    """그날 판단·실행 타임라인"""
    return reads.timeline(nh(), day_of(day), limit)


# --- market ------------------------------------------------------------------------------------------------------------

market = group('market', '시장 자료')
FILES = {'context': ('market-context.json', '시황(시장 국면·수급·테마·해외 지표)', 1800), 'movers': ('movers.json', '순위 후보', 900),
         'watchlist': ('watchlist.json', 'Jev 관심종목', 3600), 'regime': ('regime-observation.json', '국면 관측', None),
         'listings': ('new-listings.json', '신규상장 목록', None), 'premarket': ('premarket-brief.json', '장전 브리프', None)}


def file_command(name, file, text, stale):
    @market.command(name, help=text)
    @click.option('--keys', help='쉼표로 고른 항목만')
    @command
    def made(keys):
        return reads.file_answer(nh() / 'shared-etf-paper' / file, [k.strip() for k in keys.split(',')] if keys else None, stale)


for _name, (_file, _text, _stale) in FILES.items():
    file_command(_name, _file, _text, _stale)


@market.command('quote')
@click.argument('code')
@command
def market_quote(code):
    """NH 현재가·호가(외부 조회 1회)"""
    return reads.quote(code_of(code))


# --- jev ---------------------------------------------------------------------------------------------------------------

jev = group('jev', 'Jev 트레이딩 조회, 실행기 중단·재시작(승인 코드)')


@jev.command('positions')
@command
def jev_positions():
    """모의 보유와 청산 계획"""
    return reads.positions(nh())


@jev.command('events')
@click.option('--day')
@click.option('--event')
@click.option('--reason')
@click.option('--symbol')
@click.option('--limit', default=20, type=click.IntRange(1, 500))
@command
def jev_events(day, event, reason, symbol, limit):
    """실행기 이벤트(주문·체결·매도 사유·오류, 최신순)"""
    want = {'event': event, 'reason': reason, 'symbol': symbol}
    path = nh() / 'shared-etf-paper/events.jsonl'
    found = reads.tail(path, limit, day_of(day), lambda r: all(v is None or r.get(k) == v or k == 'reason' and r.get('strength_reason') == v for k, v in want.items()))
    keep = ('time', 'event', 'symbol', 'reason', 'strength_reason', 'choice', 'confidence', 'permitted', 'limit', 'qty', 'source', 'bid', 'ask', 'avg', 'peak', 'trigger')
    return Answer({'events': [{k: r[k] for k in keep if k in r} for r in found]}, [source(path)])


@jev.command('blocks')
@click.option('--day')
@command
def jev_blocks(day):
    """매수가 막힌 이유(차단 사유)별 회차 수, 많은 순"""
    from entry_reasons import ENTRY
    path, counts, strength = nh() / 'shared-etf-paper/events.jsonl', {}, {}
    found = reads.tail(path, 10 ** 9, day_of(day), lambda r: r.get('event') == 'v2_entry_gate' and r.get('permitted') is False)
    for r in found:
        for into, reason in ((counts, r.get('reason')), (strength, r.get('strength_reason'))):
            if reason:
                into[reason] = into.get(reason, 0) + 1

    def table(values):
        return [{'reason': k, 'label': ENTRY.get(k, k), 'cycles': n} for k, n in sorted(values.items(), key=lambda kv: -kv[1])]
    return Answer({'day': day_of(day), 'blocked_cycles': len(found), 'reasons': table(counts), 'strength_candidates': table(strength)}, [source(path)])


@jev.command('requests')
@command
def jev_requests():
    """대기 중인 수동 매매 요청(조회만)"""
    return reads.requests(nh())


@jev.command('restrictions')
@command
def jev_restrictions():
    """제한 규칙과 켜짐 여부"""
    import restrictions as R
    now = S.GROUPS['strategy'].read(nh())
    return {'rules': [{'rule': r[0], 'group': r[1], 'name': r[2], 'kind': R.KIND_LABELS[r[3]], **({'setting': f'strategy.{r[4]}', 'now': now[r[4]]} if r[4] in now else {})}
                      for r in R.RULES]}


@jev.command('record')
@command
def jev_record():
    """Jev 성적 요약"""
    import track_record
    return track_record.summary(nh() / 'shared-etf-paper')


@jev.command('costs')
@click.option('--day')
@command
def jev_costs(day):
    """그날 거래 비용"""
    return run_script('paper_costs.py', '--day', day_of(day))


@jev.command('explain')
@click.option('--at', help='이 시각 이후 첫 판단: HH:MM(오늘) 또는 YYYY-MM-DDTHH:MM')
@click.option('--since', help='YYYY-MM-DD')
@command
def jev_explain(at, since):
    """Jev 판단 설명(기록)"""
    if at and since:
        raise Refused('--at과 --since 중 하나만 쓰세요.', code=2)
    reads.need(nh() / 'shared-etf-paper/market-research.sqlite3')
    if at and not re.fullmatch(r'(\d{4}-\d\d-\d\dT)?\d\d:\d\d(:\d\d)?', at):
        raise Refused('--at은 HH:MM 또는 YYYY-MM-DDTHH:MM입니다.', code=2)
    at = f'{reads.today()}T{at}' if at and 'T' not in at else at  # the records hold whole times
    return run_script('jev_explain.py', '--json', *(['--at', at] if at else []), *(['--since', since] if since else []))


# --- accounts and live ---------------------------------------------------------------------------------------------------

account = group('account', '계좌 설정 보기와 묶음 변경 계획')


@account.command('list')
@command
def account_list():
    """모든 계좌(계좌번호 제외)"""
    return {'accounts': [{'account': k, 'label': r.get('label'), 'kind': r['kind'], 'engine': r['engine'], 'auto': r['auto'],
                          'live_orders': r.get('live_orders') is True} for k, r in reads.records(nh()).items()]}


@account.command('show')
@click.argument('key')
@command
def account_show(key):
    """계좌 하나의 설정"""
    found = {k: v for k, v in S.GROUPS['accounts'].read(nh()).items() if k.startswith(f'{key}.')}
    if not found:
        raise Refused(f'계좌를 찾지 못했습니다: {key}')
    return found


@account.command('shadow')
@click.option('--day')
@command
def account_shadow(day):
    """실계좌 가상 실행(미러 계산)"""
    reads.need(nh() / 'shared-etf-paper/events.jsonl')
    return run_script('nh_live_shadow.py', '--day', day_of(day))


@account.command('match')
@click.argument('live')
@click.option('--positions', default=2, type=click.IntRange(1, 5))
@click.option('--off', is_flag=True, help='맞춤 끄기')
@click.option('--reason', required=True)
@command
def account_match(live, positions, off, reason):
    """모의 금액·한도를 실계좌에 맞추는 계획"""
    return approval.make(nh(), S.ALL['match'], {'account': live, 'positions': positions, 'on': not off}, reason, '모의 운용금 맞춤')


live = group('live', '실계좌 조회, 매수 중단·재개(승인 코드)')


@live.command('status')
@command
def live_status():
    """실주문 스위치·HALT·신규 매수 중단·하루 손실 정지"""
    found = reads.status(nh())
    return Answer({k: found.data[k] for k in ('live_accounts', 'halt', 'buy_paused')}, warnings=found.warnings)


@live.command('log')
@click.argument('key')
@click.option('--day')
@click.option('--limit', default=20, type=click.IntRange(1, 200))
@command
def live_log(key, day, limit):
    """실계좌 실행기 회차 기록"""
    record = reads.records(nh()).get(key)
    if not record or record['kind'] != 'live':
        raise Refused(f'실계좌를 찾지 못했습니다: {key}')
    path = nh() / ('longterm-live.jsonl' if record['engine'] == 'longterm' else 'live-follow.jsonl')
    return Answer({'rows': reads.tail(path, limit, day_of(day), lambda r: r.get('account') == key)}, [source(path)])


@live.command('fills')
@click.argument('key')
@click.option('--day', help='YYYY-MM-DD(기본 전체)')
@click.option('--limit', default=50, type=click.IntRange(1, 1000))
@command
def live_fills(key, day, limit):
    """확인된 실계좌 체결(날짜·주문 번호순, 마지막이 최신; 시각은 주문을 보낸 때)"""
    import live_fills as L
    if (reads.records(nh()).get(key) or {}).get('kind') != 'live':
        raise Refused(f'실계좌를 찾지 못했습니다: {key}')
    found = [r for r in L.rows(nh(), key) if not day or str(r['time'])[:10] == day_of(day)]
    return Answer({'fills': found[-limit:]}, [source(nh() / 'live-fills.sqlite3')])


@live.command('review')
@command
def live_review():
    """모의 → 실계좌 적용 검토"""
    from nh_live_research_gate import evaluate
    return evaluate(nh() / 'shared-etf-paper')


@live.command('pause')
@click.option('--reason', required=True)
@command
def live_pause(reason):
    """모든 실계좌의 신규 매수 중단(바로 적용, 보유 손절은 계속)"""
    from nh_live_buy_control import buy_allowed, pause
    root = nh()
    if not buy_allowed(root):
        return {'paused': True, 'already': True}
    pause(root)
    if buy_allowed(root):
        raise OmniError('중단을 확인하지 못했습니다.', 1)
    warn = []
    try:
        if not approval.notify(f'⏸️ 실계좌 신규 매수 중단 · {agent()} · {approval.one_line(reason)}\n다시 시작은 omni live resume(승인 코드 필요)입니다.'):
            warn = ['알림을 보내지 못했습니다.']
    except Exception:  # noqa: BLE001 - the pause stands
        warn = ['알림을 보내지 못했습니다.']
    return Answer({'paused': True, 'resume': 'omni live resume --reason "…" (승인 코드 필요)'}, warnings=warn)


@live.command('off')
@click.argument('key')
@click.option('--reason', required=True)
@command
def live_off(key, reason):
    """실주문 끄기 계획(승인 필요: 손절 주문도 멈춤, 다시 켜기는 `live on`)"""
    return approval.plan_action(nh(), 'live_off', reason, account=key)


@live.command('halt')
@click.option('--reason', required=True)
@command
def live_halt(reason):
    """HALT 걸기 계획(승인 필요: 손절 주문도 멈춤)"""
    return approval.plan_action(nh(), 'halt', reason)


# --- long term ----------------------------------------------------------------------------------------------------------

lt = group('lt', '장기투자 조회, 엔진 중지(승인 코드)')
LT_DB = 'vibe-longterm/longterm.sqlite3'


def short(text, n=240):
    return text if not isinstance(text, str) or len(text) <= n else text[:n] + '…'


@lt.command('today')
@click.option('--full', is_flag=True, help='긴 판단 이유도 모두 표시')
@command
def lt_today(full):
    """오늘 판단·국면·계획"""
    found = reads.longterm_today(nh())
    for row in [] if full else found.data['recent']:
        row['reason'] = short(row['reason'])
    return found


@lt.command('levels')
@command
def lt_levels():
    """타점 지도"""
    return reads.longterm_levels(nh())


@lt.command('nav')
@click.option('--days', default=10, type=click.IntRange(1, 400))
@command
def lt_nav(days):
    """장부별 순자산"""
    found = reads.rows(nh() / LT_DB, 'SELECT day, book, value FROM nav WHERE day IN (SELECT DISTINCT day FROM nav ORDER BY day DESC LIMIT ?) ORDER BY day, book', (days,))
    return Answer({'nav': found}, [source(nh() / LT_DB)])


@lt.command('log')
@click.option('--kind')
@click.option('--detail', is_flag=True, help='지표·테마 판정·목표도 표시')
@click.option('--limit', default=20, type=click.IntRange(1, 500))
@command
def lt_log(kind, detail, limit):
    """판단 기록(regime·story·plan·order·dca·news …)"""
    kinds = [r['kind'] for r in reads.rows(nh() / LT_DB, 'SELECT DISTINCT kind FROM decisions')]
    if kind and kind not in kinds:
        raise Refused(f'없는 종류입니다: {kind}. 쓸 수 있는 값: {", ".join(sorted(kinds)) or "없음"}')
    where, args = (' WHERE kind=?', (kind,)) if kind else ('', ())
    found = reads.rows(nh() / LT_DB, f'SELECT time, day, kind, regime, mode, orders, reason{", indicators, targets" if detail else ""} FROM decisions{where} ORDER BY rowid DESC LIMIT ?', (*args, limit))
    for row in found if detail else []:
        row.update({k: json.loads(row[k]) for k in ('indicators', 'targets') if row[k]})
    return Answer({'rows': found}, [source(nh() / LT_DB)])


@lt.command('orders')
@click.option('--why', is_flag=True, help='근거 요약 포함')
@click.option('--day', help='YYYY-MM-DD(기본 전체)')
@click.option('--limit', default=10, type=click.IntRange(1, 200))
@command
def lt_orders(why, day, limit):
    """가상·계획 주문과 근거(--day로 하루만)"""
    where, args = (' WHERE day=?', (day_of(day),)) if day else ('', ())
    cols = 'time, day, book, kind, key, code, side, qty, price, filled, fill_price' + (', why, masters, summary' if why else '')
    return Answer({'orders': reads.rows(nh() / LT_DB, f'SELECT {cols} FROM evidence{where} ORDER BY rowid DESC LIMIT ?', (*args, limit))}, [source(nh() / LT_DB)])


@lt.command('watch-state')
@command
def lt_watch_state():
    """상시 감시 상태"""
    return reads.file_answer(nh() / 'vibe-longterm/watch-state.json')


# --- journal, reports, compare, research, ops -----------------------------------------------------------------------------

journal = group('journal', '매매일지')
report = group('report', '리포트')
compare = group('compare', '성과 비교')
research = group('research', '연구 기록')
ops = group('ops', '운영')
premarket = group('premarket', '장전')


@journal.command('list')
@command
def journal_list():
    """저장된 일지 날짜"""
    import trading_journal as J
    return {'days': J.day_list()}


@journal.command('show')
@click.option('--day')
@click.option('--parts', help='쉼표로 고른 항목(기본: 요약)')
@command
def journal_show(day, parts):
    """하루 일지"""
    import trading_journal as J
    found = J.load(day_of(day))
    if not found:
        raise Refused('그날 일지가 없습니다.', code=5)
    want = [p.strip() for p in parts.split(',')] if parts else [k for k in found if k not in ('charts', 'engine_news', 'headlines')]
    return {k: found[k] for k in want if k in found}


@journal.command('report')
@click.option('--day')
@command
def journal_report(day):
    """자동 리포트(저장된 일지)"""
    import trading_journal as J
    if not J.load(day_of(day)):
        raise Refused('그날 일지가 없습니다.', code=5)
    return run_script('trading_journal.py', '--report', '--no-network', '--day', day_of(day))


@report.command('list')
@command
def report_list():
    """리포트 파일 목록"""
    return {'reports': reads.documents(nh())}


@report.command('read')
@click.argument('kind')
@click.argument('key')
@click.option('--lines', type=click.IntRange(1), help='앞부분만(요약은 대개 첫 20줄)')
@click.option('--links', is_flag=True, help='링크 주소도 표시')
@command
def report_read(kind, key, lines, links):
    """리포트 본문"""
    import report_pages
    for k, found, path, *_ in report_pages.sources(nh().parent):
        if (k, found) == (kind, key):
            body = Path(path).read_text(encoding='utf-8')
            body = (body if links else re.sub(r'\[([^\]]+)\]\(https?://[^)\s]+\)', r'\1', body)).splitlines()
            return Answer({'text': '\n'.join(body[:lines]), 'lines': len(body)}, [source(path)])
    raise Refused('해당 리포트가 없습니다. `omni report list`로 확인하세요.', code=5)


@compare.command('show')
@click.option('--start')
@click.option('--end')
@click.option('--book')
@command
def compare_show(start, end, book):
    """날짜 × 장부 성과"""
    import strategy_compare
    if book and book not in strategy_compare.BOOKS:
        raise Refused(f'없는 장부입니다: {book}. 쓸 수 있는 값: {", ".join(strategy_compare.BOOKS)}')
    start, end = (day_of(d) if d else None for d in (start, end))
    return Answer({'rows': strategy_compare.rows(nh(), start=start, end=end, book=book)}, [source(nh() / 'strategy-compare.sqlite3')])


@ops.command('services')
@command
def ops_services():
    """서비스 일정과 상태(macOS·윈도우·리눅스)"""
    import launchd_services as LS
    from automation_status import LABELS
    import os_services
    try:
        found = os_services.states()
    except os_services.Unknown as exc:
        raise Refused(f'서비스 관리자에게 물어볼 수 없습니다({exc}).', code=6) from None
    names = {label.removeprefix(os_services.LABEL): name for name, label in LABELS.items()}
    rows = [{'service': n, 'name': names.get(n, n), **{k: found.get(n, os_services.MISSING)[k] for k in ('registered', 'running', 'pid', 'last_exit')},
             'schedule': LS.schedule_text(n)} for n in LS.SERVICES]
    return {'system': os_services.SYSTEM, 'services': rows}


@ops.command('service-files')
@click.option('--system', type=click.Choice(['macos', 'windows', 'linux']), required=True)
@click.option('--out', required=True, help='새로 만들 폴더')
@command
def ops_service_files(system, out):
    """자동 실행 정의 파일 만들기(등록·시작은 하지 않음)"""
    import os_services
    from .more_commands import new_file
    nh()
    folder = new_file(out)
    os_services.write(system, folder)
    return {'system': system, 'folder': str(folder), 'files': sorted(p.name for p in folder.iterdir()),
            'note': '주문 서비스(모의·실계좌)는 사용자가 확인한 뒤 직접 등록합니다.'}


@ops.command('schedule')
@click.option('--day')
@command
def ops_schedule(day):
    """그날 자동 작업(서비스·예약)"""
    import schedule_view
    import schedules
    from automation_status import processes
    return {'jobs': schedule_view.day_rows(schedules.all_jobs(dict(processes())), date.fromisoformat(day_of(day)))}


@ops.command('api')
@command
def ops_api():
    """API 호출 부하와 거절"""
    return reads.api_load(nh())


@ops.command('skills')
@command
def ops_skills():
    """프로젝트 스킬"""
    import skill_catalog
    return {'skills': [s.get('name') or s.get('key') for s in skill_catalog.SKILLS]}


@ops.command('logs')
@click.argument('service')
@click.option('--lines', default=40, type=click.IntRange(1, 500))
@command
def ops_logs(service, lines):
    """서비스 로그 끝부분"""
    import launchd_services as LS
    if service not in LS.SERVICES:
        raise Refused('서비스 이름이 아닙니다. `omni ops services`로 확인하세요.')
    folder = nh() / LS.SERVICES[service][1]
    path = folder / ('streamlit.log' if service == 'shared-etf-dashboard' else f'com.vibetrading.{service}.log')
    if not path.exists():
        raise Refused('로그가 없습니다.', code=5)
    with path.open('rb') as f:
        f.seek(max(0, path.stat().st_size - 200_000))
        found = f.read().decode('utf-8', 'replace').splitlines()[-lines:]
    return Answer({'lines': found}, [source(path)])


# (group, command, script, arguments, options taken as the value after a flag, help): every one only reads
PASS = [(market, 'brief', 'vibe_market_brief.py', [], {}, 'ETF 7종 시세와 실행기 요약(외부 조회)'),
        (premarket, 'status', 'premarket.py', ['--status'], {}, '장전 브리프 상태'),
        (premarket, 'template', 'premarket.py', ['--report'], {}, '장전 분석 틀'),
        (premarket, 'preflight', 'preflight.py', ['--no-send'], {}, '장전 사전 점검(발송 없음, 외부 조회, 점검 기록 한 줄이 남음)'),
        (lt, 'report-blank', 'longterm_report.py', ['--report', '--blank'], {'--day': 'day'}, '전략 리포트 틀(사실 표)'),
        (lt, 'report-check', 'longterm_report.py', ['--check'], {'--day': 'day'}, '전략 리포트 검사'),
        (lt, 'audit-blank', 'longterm_audit.py', [], {'--blank': 'key'}, '복기·감사 틀'),
        (lt, 'audit-check', 'longterm_audit.py', [], {'--check': 'key'}, '복기·감사 검사'),
        (lt, 'report-status', 'longterm_report.py', ['--status'], {'--day': 'day'}, '전략 리포트 상태'),
        (lt, 'report-data', 'longterm_report.py', ['--data'], {'--day': 'day'}, '전략 리포트 자료'),
        (lt, 'audit-data', 'longterm_audit.py', [], {'--data': 'key'}, '복기·감사 자료'),
        (lt, 'audit-status', 'longterm_audit.py', ['--status'], {}, '감사 상태'),
        (lt, 'ideas', 'us_ideas.py', ['--status'], {}, '미국 종목 아이디어 상태(참고, 주문 없음)'),
        (journal, 'status', 'trading_journal.py', ['--status'], {'--day': 'day'}, '일지 상태'),
        (journal, 'due', 'journal_template.py', [], {'--due': 'day'}, '그날 작성할 기간 일지'),
        (journal, 'week', 'trading_journal.py', ['--week'], {'--day': 'day'}, '이번 주 장부별 성적'),
        (ops, 'notify-preview', 'discord_notify.py', ['--once', '--dry-run'], {}, '보내지 않은 알림 미리보기(발송 없음)'),
        (journal, 'improvement', 'daily_improvement.py', [], {'--day': 'day'}, '장 마감 개선안 자료'),
        (report, 'pages', 'report_pages.py', ['--list'], {}, '발행 기록'),
        (report, 'settings', 'report_pages.py', ['--settings'], {}, '발행 설정(값은 표시하지 않음)'),
        (compare, 'check', 'strategy_compare.py', ['--check'], {}, '성과 자료 점검'),
        (research, 'issues', 'issue_candidates.py', ['--summary'], {}, '이슈 그림자 후보 성적'),
        (research, 'ipo', 'ipo_shadow.py', ['--summary'], {}, '신규주 그림자 성적'),
        (research, 'oversold', 'oversold_outcomes.py', [], {'--summary': 'days'}, '낙폭과대 사후 결과'),
        (research, 'nxt', 'nxt_virtual.py', ['--audit'], {}, 'NXT 가상 매매 감사'),
        (ops, 'incidents', 'audit_incidents.py', [], {'--since': 'since'}, '문제 재발 이력'),
        (ops, 'doctor', 'state_sync.py', ['check'], {}, '새 기기·계정 점검')]


def passthrough(where, name, script, fixed, options, text):
    def body(**given):
        args = list(fixed)
        for flag, field in options.items():
            value = given.get(field)
            if value is None and (field == 'key' or flag == '--due'):
                value = reads.today()
            if value is None:
                continue
            if field in ('day', 'since') or flag == '--due':
                value = day_of(value)
            elif not KEY.fullmatch(str(value)) or (field == 'key' and not PERIOD.fullmatch(str(value))) or (field == 'days' and not str(value).isdigit()):
                raise Refused(f'{field} 값이 올바르지 않습니다: {value}', code=2)
            args += [flag, str(value)]
        backend.nhplug()
        got = run_script(script, *args, ok=(0, 1) if script == 'preflight.py' or name.endswith('-check') else (0,))
        return Answer(got, code=1) if name.endswith('check') and isinstance(got, dict) and got.get('ok') is False else got  # some checks answer 0
    body.__doc__ = text
    made = command(body)
    for flag, field in options.items():
        made = click.option(f'--{field}', default='30' if field == 'days' else None)(made)
    where.command(name, help=text)(made)


for _row in PASS:
    passthrough(*_row)


@cli.command('query')
@click.argument('database')
@click.argument('sql')
@click.option('--limit', default=200, type=click.IntRange(1, 5000))
@command
def query(database, sql, limit):
    """nhplug 아래 DB를 읽기 전용 SELECT 한 문장으로 조회"""
    root = nh().resolve()
    path = (root / database).resolve()
    if root not in path.parents or not path.is_file() or path.suffix != '.sqlite3':
        raise Refused('DB는 ~/.vibe-trading/nhplug 아래 .sqlite3 파일이어야 합니다.')
    if sql.lstrip().split(None, 1)[:1] not in (['SELECT'], ['select'], ['WITH'], ['with']) or ';' in sql.strip().rstrip(';'):
        raise Refused('SELECT 또는 WITH 한 문장만 실행할 수 있습니다.')
    import sqlite3
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        db.execute('PRAGMA query_only=1')
        db.row_factory = sqlite3.Row
        found = [dict(r) for r in db.execute(sql).fetchmany(limit)]
    except sqlite3.Error as exc:
        raise Refused(f'조회하지 못했습니다: {type(exc).__name__}') from None
    finally:
        db.close()
    return Answer({'rows': found}, [source(path)])


# --- settings -------------------------------------------------------------------------------------------------------------

settings = group('settings', '설정 조회·계획·적용')


def shown(address, spec, value):
    out = {'setting': address, 'now': value, 'default': spec.default, 'label': spec.label}
    if spec.low is not None:
        out['range'] = f'{spec.low}~{spec.high}'
    if spec.choices:
        out['choices'] = ', '.join(map(str, spec.choices))
    return out | ({'takes_effect': spec.effect} if spec.effect else {})


@settings.command('list')
@click.option('--group', 'name', type=click.Choice(list(S.GROUPS)))
@click.option('--changed', is_flag=True, help='기본값과 다른 것만')
@click.option('--find', 'word', help='주소·이름 검색어(여러 개면 모두 포함한 것)')
@click.argument('more', nargs=-1)
@command
def settings_list(name, changed, word, more):
    """설정과 기본값·범위"""
    if more and not word:
        raise Refused('검색어는 --find 뒤에 적으세요.', code=2)
    root, out, warn, words = nh(), [], [], [w.lower() for w in f"{word or ''} {' '.join(more)}".split()]
    try:
        labels = {'actual': '장기투자 가상 계좌', **{k: r.get('label', k) for k, r in reads.records(root).items()}}
    except Refused:
        labels = {}
    for g in ([S.GROUPS[name]] if name else S.GROUPS.values()):
        try:
            values = g.read(root)
            specs = g.specs(root)
        except Refused as exc:
            warn.append(f'{g.name}: {exc}')
            continue
        for k, spec in specs.items():
            now = values.get(k)
            if (changed and now == spec.default) or not all(w in f'{g.name}.{k} {spec.label} {labels.get(k.split(".")[0], "") if g.name in ("accounts", "book", "dca") else ""}'.lower() for w in words):
                continue
            out.append(shown(f'{g.name}.{k}', spec, now))
    if words and not out:
        warn.append('검색어를 모두 포함한 설정이 없습니다. 검색어를 줄여 보세요.')
    return Answer({'settings': out}, warnings=warn)


@settings.command('get')
@click.argument('addresses', nargs=-1, required=True)
@command
def settings_get(addresses):
    """설정 값"""
    root, out = nh(), []
    for address in addresses:
        g, key = S.split(address)
        out.append(shown(address, S.spec_of(g, root, key), g.read(root).get(key)))
    return {'settings': out}


@settings.command('explain')
@click.argument('address')
@command
def settings_explain(address):
    """범위·실계좌 영향·최근 변경"""
    root = nh()
    g, key = S.split(address)
    spec, value = S.spec_of(g, root, key), g.read(root).get(key)
    level, affects = S.level(root, [[address, value, True if spec.kind == 'bool' else None]])
    import config_store
    recent = [{'time': t, 'before': o, 'after': n, 'who': w} for t, kind, k, o, n, w in config_store.history(3000, g.kind) if S.find(k, kind) == address][:5]
    return {**shown(address, spec, value), 'level': level, 'affects': affects, 'how': S.reach(root, affects), 'files': [p.name for p in g.files(root)], 'recent': recent}


@settings.command('plan')
@click.argument('pairs', nargs=-1, required=True)
@click.option('--reason', required=True, help='변경 이유(이력에 남음)')
@command
def settings_plan(pairs, reason):
    """`주소=값`을 검사해 계획으로 저장(설정은 바꾸지 않음)"""
    root, chosen, changes = nh(), None, {}
    for pair in pairs:
        address, eq, raw = pair.partition('=')
        if not eq:
            raise Refused(f'주소=값 형식이 아닙니다: {pair}', code=2)
        g, key = S.split(address)
        if chosen and g is not chosen:
            raise Refused('한 계획은 한 묶음만 바꿉니다(두 파일을 함께 저장하다가 중간에 멈출 수 있습니다).')
        chosen, changes[key] = g, S.parse(S.spec_of(g, root, key), raw, address)
    return approval.make(root, chosen, changes, reason)


@settings.command('preset')
@click.argument('name', type=click.Choice(['strength', 'ipo', 'volume', 'oversold']))
@click.argument('choice')
@click.option('--reason', required=True)
@command
def settings_preset(name, choice, reason):
    """빠른 선택으로 계획 만들기"""
    root = nh()
    import strategy_settings as ST
    table = {'strength': ST.STRENGTH_PRESETS, 'ipo': ST.IPO_PRESETS, 'oversold': ST.OVERSOLD_PRESETS,
             'volume': {k: {'entry_volume_ratio': v} for k, v in ST.VOLUME_PRESETS.items()}}[name]
    if choice not in table:
        raise Refused(f'{" · ".join(table)} 중 하나입니다.')
    return approval.make(root, S.GROUPS['strategy'], {k: float(v) for k, v in table[choice].items()}, reason, f'빠른 선택 {name} {choice}')


@settings.command('plans')
@command
def settings_plans():
    """최근 계획"""
    return {'plans': approval.plans(nh())}


@settings.command('show-plan')
@click.argument('plan')
@command
def settings_show_plan(plan):
    """계획 보기"""
    if plan.startswith('schedule-'):
        from schedule_migration import verify
        return _schedule_call(verify, plan)
    return approval.show(nh(), plan)


@settings.command('approve')
@click.argument('plan')
@command
def settings_approve(plan):
    """승인 코드를 알림 채널로 보냄(적용하지 않음)"""
    return approval.approve(nh(), plan)


@settings.command('apply')
@click.argument('plan')
@click.option('--code', help='알림으로 받은 승인 코드')
@click.option('--phrase', help='확인 문구(계획에 나온 그대로)')
@click.option('--during-market', is_flag=True)
@command
def settings_apply(plan, code, phrase, during_market):
    """계획 적용(실계좌에 영향을 주면 승인 코드 필요)"""
    if plan.startswith('schedule-'):
        from schedule_migration import apply
        return _schedule_call(apply, plan)
    return approval.apply(nh(), plan, code, agent(), during_market, phrase)


@settings.command('history')
@click.option('--group', 'kind')
@click.option('--since', help='YYYY-MM-DD')
@click.option('--limit', default=30, type=click.IntRange(1, 1000))
@command
def settings_history(kind, since, limit):
    """변경 이력(번호로 되돌리기)"""
    path = nh() / 'settings-history.sqlite3'
    where, args = [], []
    if kind and kind not in S.GROUPS and kind not in {g.kind for g in S.ALL.values()}:
        raise Refused(f'없는 묶음입니다: {kind}. 묶음: {", ".join(S.GROUPS)}')
    if kind:
        where.append('kind=?'), args.append(S.GROUPS[kind].kind if kind in S.GROUPS else kind)
    if since:
        where.append('time>=?'), args.append(day_of(since))
    found = reads.rows(path, 'SELECT rowid AS id, time, kind, key, old, new, who FROM changes' + (' WHERE ' + ' AND '.join(where) if where else '')
                       + ' ORDER BY rowid DESC LIMIT ?', (*args, limit))
    return Answer({'changes': [{k: v for k, v in r.items() if k != 'key'} | {'setting': S.find(r['key'], r['kind']) or f"{r['kind']}:{r['key']}"}
                               for r in found if not r['key'].endswith(('.hash', '.masked'))]}, [source(path)],
                  ['--limit 개수만큼만 표시했습니다. 더 있을 수 있습니다.'] if len(found) == limit else [])


@settings.command('revert')
@click.argument('change', type=int)
@click.option('--reason', required=True)
@command
def settings_revert(change, reason):
    """이력 하나를 되돌리는 계획"""
    root = nh()
    found = reads.rows(root / 'settings-history.sqlite3', 'SELECT kind, key, old, new FROM changes WHERE rowid=?', (change,))
    if not found:
        raise Refused('해당 이력이 없습니다.')
    row = found[0]
    address = S.find(row['key'], row['kind'])
    if not address or row['old'] is None:
        raise Refused('이 이력은 설정 하나의 이전 값이 아니라 되돌릴 수 없습니다.')
    g, key = S.split(address)
    S.spec_of(g, root, key)
    if g.read(root).get(key) != json.loads(row['new']):
        raise Refused('그 뒤에 값이 다시 바뀌었습니다. 지금 값을 확인하고 새로 계획하세요.')
    old = json.loads(row['old'])
    return approval.make(root, g, {key: float(old) if g.name == 'strategy' else old}, reason, f'이력 {change} 되돌리기')


@settings.command('export')
@click.option('--group', 'name', type=click.Choice(list(S.GROUPS)))
@command
def settings_export(name):
    """설정 값 내보내기(계좌번호 제외)"""
    root = nh()
    return {g.name: g.read(root) for g in ([S.GROUPS[name]] if name else S.GROUPS.values())}


LIST_OPS = {'watch': '관심종목', 'universe': '낙폭과대 감시', 'dcalist': '적립식 관심목록'}


@settings.command('list-edit')
@click.argument('which', type=click.Choice(list(LIST_OPS)))
@click.argument('op', type=click.Choice(['add', 'remove']))
@click.argument('code')
@click.option('--name', default='')
@click.option('--account', 'key', default='paper-shared')
@click.option('--reason', required=True)
@command
def settings_list_edit(which, op, code, name, key, reason):
    """관심종목·감시 종목·적립식 관심목록 편집 계획"""
    code_of(code)
    root = nh()
    if which == 'watch':
        if key not in reads.records(root):
            raise Refused(f'계좌를 찾지 못했습니다: {key}')
        changes = {f'{key}.{op}.{code}': name or code}
    else:
        changes = {code: (name or code) if op == 'add' else None}
    return approval.make(root, S.ALL[which], changes, reason, f'{LIST_OPS[which]} {op}')


@cli.command('engine')
@click.argument('mode', type=click.Choice(['both', 'jev', 'vibe', 'off']))
@click.option('--reason', required=True)
@command
def engine(mode, reason):
    """운영 엔진 변경 계획(두 파일이라 계획이 나뉠 수 있음)"""
    import engine_ops
    root = nh()
    now, want = engine_ops.status(root), dict(zip(('jev', 'vibe'), engine_ops.MODES[mode]))
    out = []
    if now['jev'] != want['jev']:
        out.append(approval.make(root, S.GROUPS['accounts'], {'paper-shared.auto': want['jev']}, reason, f'운영 엔진 {engine_ops.LABELS[mode]}'))
    if now['vibe'] != want['vibe']:
        out.append(approval.make(root, S.GROUPS['lt'], {'enabled': want['vibe']}, reason, f'운영 엔진 {engine_ops.LABELS[mode]}'))
    if not out:
        raise Refused('이미 그 상태입니다.')
    return {'plans': out}


# --- guide ----------------------------------------------------------------------------------------------------------------

def commands(node=cli, path=()):
    for name, cmd in sorted(node.commands.items()):
        if isinstance(cmd, click.Group):
            yield from commands(cmd, (*path, name))
        else:
            yield {'command': ' '.join((*path, name)), 'group': node.help or '', 'help': (cmd.help or '').split('\n')[0],
                   'options': ' '.join(o for p in cmd.params for o in (p.opts if p.param_type_name == 'option' else [p.name]))}


@cli.command('guide')
@click.argument('words', nargs=-1)
@command
def guide(words):
    """모든 명령 보기와 검색"""
    every = list(commands())
    found = [c for c in every if all(w.lower() in f"{c['command']} {c['help']} {c.get('group', '')}".lower() for w in words)]
    warn = [] if found else ['검색어를 포함한 명령이 없습니다. 전체 명령은 `omni guide`로, 설정은 `omni settings list --find <검색어>`로 찾으세요.']
    return Answer({'commands': found, 'rules': ['직접 주문하는 명령은 없습니다. 매매 요청(trade)은 사용자가 승인 코드를 알려 준 뒤 실행기·미러가 가드를 거쳐 처리합니다.', '설정은 plan → (H면 approve → 사용자가 코드 확인) → apply 순서입니다.',
                                         '승인 코드를 추측하거나 DB를 고치지 않습니다.', '자세한 사용법은 docs/omni-cli.md, 명령·설정 목록은 docs/omni-cli-reference.md입니다.', '종료 코드: 0 성공 1 실패 2 사용법 3 승인 필요 4 거절 5 자료 없음·오래됨 6 외부 조회 실패']}, warnings=warn)


from . import more_commands  # noqa: E402,F401 - registers the rest on the groups above (and action_commands)


def main():
    cli.main(prog_name='omni')


schedule = group('schedule', '예약 작업 목록·백업·AI 실행 프로필 이전')


def _schedule_call(fn, *args):
    from state_sync import runtime_root
    try:
        return fn(runtime_root(), *args)
    except (OSError, ValueError) as exc:
        raise Refused(str(exc)) from exc


@schedule.command('list')
@command
def schedule_list():
    """예약 작업의 일정·모델·프롬프트 보존 상태 조회"""
    from schedule_bundle import inventory
    return {'jobs': [{k: v for k, v in job.items() if k != 'prompt'} for job in _schedule_call(inventory)]}


@schedule.command('export')
@click.option('--out', required=True, type=click.Path(dir_okay=False, path_type=Path), help='새 번들 파일 경로')
@command
def schedule_export(out):
    """비공개 파일에 예약 정의 내보내기"""
    from schedule_bundle import export_bundle
    return _schedule_call(export_bundle, out)


@schedule.command('profiles')
@command
def schedule_profiles():
    """등록된 AI 실행 프로필 조회"""
    from schedule_migration import profiles
    return {'profiles': _schedule_call(profiles)}


@schedule.command('profile-add')
@click.argument('provider', type=click.Choice(['claude', 'codex', 'antigravity']))
@click.argument('alias')
@click.option('--model', required=True, help='실행할 정확한 모델 ID')
@click.option('--config-dir', type=click.Path(file_okay=False, path_type=Path), help='선택한 CLI 인증 설정 폴더')
@click.option('--credential-file', type=click.Path(dir_okay=False, path_type=Path), help='Antigravity 전용 Gemini API 키 파일')
@command
def schedule_profile_add(provider, alias, model, config_dir, credential_file):
    """현재 로그인 계정을 작업 실행 프로필로 등록"""
    from schedule_migration import register_profile
    return _schedule_call(register_profile, provider, alias, model, config_dir, credential_file)


@schedule.command('probe')
@click.argument('profile')
@command
def schedule_probe(profile):
    """검증용 입력으로 지정 모델 호출 확인(사용량 발생)"""
    from schedule_migration import probe
    return _schedule_call(probe, profile)


@schedule.command('plan')
@click.option('--to', 'target', required=True, help='대상 프로필 ID')
@click.option('--from', 'bundle_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), help='내보낸 예약 번들')
@click.option('--job', multiple=True, help='이전할 작업 ID(반복 가능)')
@click.option('--all', 'all_jobs', is_flag=True, help='전체 작업을 계획에 포함')
@click.option('--receipt', type=click.Path(dir_okay=False, path_type=Path), help='원본에서 받은 인수 영수증')
@click.option('--source-key', help='원본 기기에서 확인한 서명 지문')
@command
def schedule_plan(target, bundle_path, job, all_jobs, receipt, source_key):
    """전체 또는 선택한 예약 작업의 이전 계획 작성"""
    from schedule_bundle import read_bundle
    from schedule_migration import create_plan
    if all_jobs == bool(job):
        raise Refused('--all 또는 --job <작업 ID>를 선택하세요.', code=2)
    try:
        bundle = read_bundle(bundle_path) if bundle_path else None
        handoff = json.loads(receipt.read_text(encoding='utf-8')) if receipt else None
    except (ValueError, OSError) as exc:
        raise Refused(str(exc)) from exc
    return _schedule_call(create_plan, target, bundle, job, handoff, source_key)


@schedule.command('device')
@command
def schedule_device():
    """이전용 기기 ID와 서명 지문 확인"""
    from schedule_handoff import device_info
    return _schedule_call(device_info)


@schedule.command('handoff')
@click.option('--to-device', required=True, help='인수할 기기의 ID')
@click.option('--job', required=True, multiple=True, help='원본 실행권을 해제할 작업 ID')
@click.option('--out', required=True, type=click.Path(dir_okay=False, path_type=Path), help='서명된 인수 영수증 파일')
@command
def schedule_handoff(to_device, job, out):
    """원본 실행권을 해제하고 지정 기기용 인수 영수증 발급"""
    from schedule_handoff import export_receipt, prepare
    if out.exists() or not out.parent.is_dir():
        raise Refused('새 영수증을 저장할 경로를 지정하세요.')
    receipt = _schedule_call(prepare, to_device, job)
    try:
        return _schedule_call(export_receipt, receipt['transfer_id'], out)
    except Refused as exc:
        raise Refused(f"원본 실행권은 해제됐습니다. `omni schedule receipt-export {receipt['transfer_id']} --out <파일>`로 다시 저장하세요.") from exc


@schedule.command('receipt-export')
@click.argument('transfer_id')
@click.option('--out', required=True, type=click.Path(dir_okay=False, path_type=Path), help='새 인수 영수증 파일 경로')
@command
def schedule_receipt_export(transfer_id, out):
    """기발급 영수증을 별도 파일로 다시 저장"""
    from schedule_handoff import export_receipt
    return _schedule_call(export_receipt, transfer_id, out)


@schedule.command('validate-model')
@click.argument('plan_id')
@click.argument('job_id')
@click.option('--fixture', required=True, type=click.Path(dir_okay=False, path_type=Path), help='input과 expected를 담은 비식별 JSON 자료')
@command
def schedule_validate_model(plan_id, job_id, fixture):
    """비식별 입력과 기대 결과로 대체 모델 비교(사용량 발생)"""
    from schedule_migration import validate_model
    return _schedule_call(validate_model, plan_id, job_id, fixture)


@schedule.command('verify')
@click.argument('plan_id')
@command
def schedule_verify(plan_id):
    """이전 상태와 첫 회차 기록 조회"""
    from schedule_migration import verify
    return _schedule_call(verify, plan_id)


@schedule.command('rollback')
@click.argument('plan_id')
@command
def schedule_rollback(plan_id):
    """이전에서 바꾼 Omni 실행 경로 되돌리기"""
    from schedule_migration import rollback
    return _schedule_call(rollback, plan_id)


if __name__ == '__main__':
    main()
