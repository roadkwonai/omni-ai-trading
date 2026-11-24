"""Read-only answers from the dashboard's trading records and broker balance."""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from engine_roles import SHARED
from sqlite_ro import ro_connect

KST = ZoneInfo('Asia/Seoul')
logger = logging.getLogger(__name__)
SUPPORTED_QUESTIONS = (
    '이번 주 Jev 모의 수익률과 최대 낙폭은?',
    '오늘 모의 체결 내역은?',
    '오늘 실계좌 1 체결 내역은?',
    '실계좌 1의 현재 보유 종목과 잔고는?',
    '모의계좌 보유 현황은?',
    '지금 실행기 상태는?',
)
PERIODS = ('지난주', '이번주', '지난달', '이번달', '어제', '오늘')
SAME_PERIOD = {'저번주': '지난주', '금주': '이번주', '저번달': '지난달', '금월': '이번달', '전날': '어제', '금일': '오늘'}
UNSUPPORTED_PERIOD = re.compile(
    r'\d{4}[-./년]|\d{1,2}월|\d{1,2}일|\d{1,2}/\d{1,2}|작년|다음[주달]|내일|최근|연초|올해|지난거래일|직전거래일'
    r'|전체(?:기간|수익|성과|손익|체결)|총수익|누적|그동안|지금까지|일주일|주간|월간|한달|개월|분기|반기|그제|그저께|이틀|사흘|[월화수목금]요일',
)
# questions about how something works or where it is, not about the stored numbers
EXPLAIN = re.compile(
    r'어디|어느(?:화면|탭|메뉴)|사용법|뜻|왜|이유|조건|역할|이란|의미|정의|설명|방법|원리|공식|전망|뉴스|기준(?:은|이|을|$)'
    r'|확인하는법|조회하는법|보는법|비교할때|비교하려면|csv|내려받|어떻게(?!돼|되|됐|나왔|나와)',
)
WHAT = re.compile(r'무엇인가요|뭐야|뭐예요|뭔가요')  # a definition, unless a date or an account number makes it a lookup
LEAD = rf"(?:그럼|지금|현재|혹시|{'|'.join(PERIODS)})*"  # what may come before an own-record word when nothing names its owner
OWN = re.compile(r'계좌|모의|jev|제브(?!라)')
# how such a question may end: "보유 종목은?" is ours, "보유 종목 배당 일정" is not
END = (r'(?:은|는|이|가|도|을|를|의)?(?:총|모두|전부|좀|다|지금|현재)*(?:얼마\w{0,5}|어때\w{0,2}|어떤가요|어떻게(?:돼|되|됐|나왔|나와)\w{0,4}'
       r'|(?:알려|보여|말해)\w{0,4}|조회\w{0,4}|확인\w{0,4}|뭐\w{0,4}|무엇\w{0,4}|있\w{0,4}|몇\w{0,6}|요)?')
BARE_HOLDING = re.compile(LEAD + r'(?:(?:보유|들고있)(?:하고있는|하는|중인|중|한|는)?(?:종목|현황|주식|내역|목록)?(?:수|개수)?|포지션(?:현황)?'
                          r'|(?:뭐|무엇을|어떤종목|무슨종목)(?:을|를)?(?:보유|들고)\w{0,6}|[^\s]{1,12}(?<![가이])몇주(?:를|나)?(?:보유|들고)\w{0,6})' + END)
BARE_TRADE = re.compile(LEAD + r'(?:산|판|매수한|매도한|뭐|어떤종목|무슨종목)')
RESULT_WORD = r'(?:수익률?|손익|성과|(?:최대)?낙폭|승률)'
BARE_RESULT = re.compile(LEAD + rf'(?:공유)?(?:모의(?:계좌|투자)?)?(?:의)?{RESULT_WORD}(?:(?:과|와|이랑|하고|및|,|·){RESULT_WORD})*' + END)
OTHERS = re.compile(r'외국인|기관|국민연금|주주|버핏|버크셔|etf|kodex|tiger|펀드|은행|금융')  # whose holdings or trades the records do not keep
OTHER_BOOK = re.compile(r'장기투자|nxt|vibe|가상|퇴직연금|isa|irp|cma')
NEWS_FILL = re.compile(r'체결강도|체결가|체결알림|체결통보|체결수수료|체결속도|체결방식|체결률|계약|협정|fta|mou|공시|외국인|기관|시간외')
JEV_PART = re.compile(r'낙폭과대|낙주|선별|신규주|신규상장|인버스|레버리지|관심종목|강도|스윙|종가|코스닥|나스닥|장기투자')  # the ledger keeps the whole account
RESULT = ('성과', '수익', '손익', '낙폭', '성능', '승률')
NOT_PAPER = r'(?<!모의)(?<!모의 )'
# read from the question itself: without its spaces "실계좌 1 5개" would be account 15
NUMBER = re.compile(
    NOT_PAPER + r'계좌\s*([1-9]\d?)(?!\d|\s*개)번?(?:\s*(?:,|와|과|이랑|랑|하고|·|및|\s)\s*(?:실?\s*계좌\s*)?([1-9])번?(?!\d|\s*[개주월일건위시분%]))?'
    r'|(?<!\d)([1-9])번\s*(?:실\s*)?계좌')
TAIL = re.compile(r'(?:은|는|도|을|를|이|가)?(?:요|얼마\S*|알려\S*|보여\S*)?$')
Kind = Literal['performance', 'fills', 'positions', 'status', 'unsupported']


@dataclass(frozen=True)
class InternalQuery:
    kind: Kind
    period: str = '오늘'
    account: str = 'paper'
    compare_kodex: bool = False


def resolve(question: str, *, previous: InternalQuery | None = None) -> InternalQuery | None:
    q = ''.join(question.casefold().split()).rstrip('?!.')
    for said, period in SAME_PERIOD.items():
        q = q.replace(said, period)
    if previous and previous.kind != 'unsupported' and (found := _follow_up(TAIL.sub('', q.removeprefix('그럼')), previous)):
        return found

    periods = [value for value in PERIODS if value in q]
    period = periods[0] if periods else '오늘'
    accounts = {number for match in NUMBER.finditer(question) for number in match.groups() if number}
    kodex = 'kodex200' in q or '069500' in q
    if EXPLAIN.search(q) or (WHAT.search(q) and not (periods or accounts or re.match('지금|현재', q))) or ('차이' in q and not kodex):
        return None

    own = OWN.search(q)
    mine = re.search(r'(?:^|\s)내(?:가|\s*(?:보유|종목|포지션|계좌))', question)
    live = bool(accounts) or '실계좌' in q or (bool('실제체결' in q or (mine and '내계좌' in q)) and '모의' not in q)
    account = f'live-{next(iter(accounts))}' if accounts else 'live' if live else 'paper'
    inherited = bool(previous and previous.account.startswith('live-') and not live and not own)
    holding = any(word in q for word in ('보유현황', '보유종목', '보유하고있', '보유중', '들고있', '포지션')) or (
        '보유' in q and '계좌' in q and any(word in q for word in ('몇주', '어떤종목', '있어', '알려', '보여')))
    named = bool(accounts or own or mine)
    if re.search(r'^(?:지금|현재)*운영상태', q) or ('실행기' in q or '런치드' in q) and any(
        word in q for word in ('상태', '돌고', '실행중', '켜져', '멈춰', '멈췄', '운영중')
    ):
        kind: Kind = 'status'
    elif live and any(word in q for word in ('잔고', '잔액', '예수금', '총자산', '주문가능금액', '평가금액')):
        kind = 'positions'
    elif holding and (named or not OTHERS.search(q) and BARE_HOLDING.fullmatch(q)):
        kind = 'positions'
    elif '미체결' in q:
        if not any(word in q for word in ('계좌', '내역', '있', '몇', '보여')):
            return None
        kind = 'unsupported'
    elif any(word in q for word in ('체결', '거래내역', '매매내역')) and not NEWS_FILL.search(q) and (
        periods or any(word in q for word in ('계좌', '내역', '기록', '종목', '보여', '알려'))
    ):
        kind = 'fills'
    elif any(word in q for word in ('산종목', '판종목', '매수한종목', '매도한종목', '뭐샀', '뭐팔')) and not NEWS_FILL.search(q) and (
        named or BARE_TRADE.match(q)
    ):
        kind = 'fills'
    elif re.search(r'jev|제브(?!라)', q) and any(word in q.replace('낙폭과대', '') for word in RESULT):
        whole = not JEV_PART.search(q) and (kodex or '비교' not in q.replace('성과비교', ''))
        kind = 'performance' if whole else 'unsupported'
    elif BARE_RESULT.fullmatch(q):
        kind = 'performance'
    elif ('실계좌' in q or '모의계좌' in q or (mine and '내계좌' in q)) and any(
        word in q for word in ('잔고', '수익', '손익', '성과', '잔액', '예수금', '총자산', '평가', '계좌번호')
    ):
        kind = 'unsupported'
    else:
        return None
    if kind == 'unsupported' or len(periods) > 1 or len(accounts) > 1 or ('실계좌' in q and '모의계좌' in q):
        return InternalQuery('unsupported')
    if UNSUPPORTED_PERIOD.search(q) or OTHER_BOOK.search(q) or (kind in ('status', 'positions') and period != '오늘'):
        return InternalQuery('unsupported')
    if kind == 'performance' and (account != 'paper' or inherited):
        return InternalQuery('unsupported')
    if inherited and kind in ('fills', 'positions'):
        account = previous.account
    return InternalQuery(kind, period, account, kind == 'performance' and kodex)


def _follow_up(q: str, previous: InternalQuery) -> InternalQuery | None:
    """A short question that only changes what the previous internal answer was about."""
    if q in ('다시조회', '다시조회해줘', '새로고침'):
        return previous
    if previous.kind in ('performance', 'fills') and q in PERIODS:
        return replace(previous, period=q)
    if previous.kind == 'performance':
        if q in ('수익률', '손익', '최대낙폭', '낙폭'):
            return previous
        if re.fullmatch(r'kodex200(?:과|이랑|하고|도)?비교(?:해줘)?', q):
            return replace(previous, compare_kodex=True)
    if previous.kind in ('fills', 'positions'):
        if number := re.fullmatch(r'(?:실계좌|계좌)?([1-9]\d?)번?(?:계좌)?(?:로)?(?:조회)?(?:해줘)?', q):
            return replace(previous, account=f'live-{number[1]}')
        if q in ('모의', '모의계좌'):
            return replace(previous, account='paper')
    if previous.kind == 'positions' and previous.account.startswith('live-') and q in (
        '잔고', '예수금', '현금', '주문가능금액', '총자산', '보유평가금액', '평가금액', '보유종목',
    ):
        return previous
    return None


def bounds(period: str, today: date) -> tuple[str, str]:
    end = today
    if period == '어제':
        start = end = today - timedelta(days=1)
    elif period in ('이번주', '지난주'):
        start = today - timedelta(days=today.weekday())
        if period == '지난주':
            end = start - timedelta(days=1)
            start -= timedelta(days=7)
    elif period in ('이번달', '지난달'):
        start = today.replace(day=1)
        if period == '지난달':
            end = start - timedelta(days=1)
            start = end.replace(day=1)
    else:
        start = today
    return start.isoformat(), end.isoformat()


def _stamp(text: str | None) -> str:
    return (text or '기록 없음')[:19].replace('T', ' ')


def _revision(path: Path, *, wal: bool = False) -> tuple[tuple[int, int, int] | None, ...]:
    stamps = []
    for file in (path, path.with_name(path.name + '-wal')) if wal else (path,):
        try:
            stat = file.stat()
        except FileNotFoundError:
            stamps.append(None)
        else:
            stamps.append((stat.st_ino, stat.st_mtime_ns, stat.st_size))
    return tuple(stamps)


@lru_cache(maxsize=64)
def _performance_rows(runtime: Path, start: str, end: str, _revision_key: tuple):
    import strategy_compare

    return strategy_compare.rows(runtime, start=start, end=end, book='jev', timeout=2)


@lru_cache(maxsize=32)
def _holdings(path: Path, _revision_key: tuple) -> tuple[str, tuple[tuple[str, str, int], ...]]:
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    stamp = datetime.fromisoformat(snapshot['time']).astimezone(KST)
    holdings = tuple((str(code), str(row.get('iem_nm') or ''), int(row['rsdl_qty']))
                     for code, row in sorted(snapshot['holdings'].items()))
    return stamp.strftime('%Y-%m-%d %H:%M:%S'), holdings


@lru_cache(maxsize=64)
def _fills(path: Path, start: str, end: str, account: str, _revision_key: tuple):
    with ro_connect(path, 2) as db:
        if account == 'paper':
            return tuple(db.execute(
                'SELECT time, symbol, side, quantity, price FROM fills WHERE time >= ? AND time < ? '
                'ORDER BY time DESC, rowid DESC LIMIT 20',
                (start, (date.fromisoformat(end) + timedelta(days=1)).isoformat()),
            ))
        return tuple(db.execute(
            'SELECT time, symbol, side, quantity, price FROM fills WHERE account = ? AND day BETWEEN ? AND ? '
            'ORDER BY day DESC, order_no DESC LIMIT 20', (account, start, end),
        ))


def _performance(query: InternalQuery, root: Path, now: datetime) -> str:
    from strategy_compare import FILE, stats

    start, end = bounds(query.period, now.date())
    revision = _revision(root / FILE, wal=True)
    if revision[0] is None:
        return '### Jev 성과 비교\n\n저장된 성과 비교 원장을 아직 확인할 수 없습니다.'
    rows = _performance_rows(root, start, end, revision)
    result = stats(rows, 'jev')
    if result is None:
        return f'### Jev 성과 비교\n\n{start} ~ {end} 기간에 저장된 성과 기록이 없습니다.'
    sources = ', '.join(sorted({str(r['source'] or '기록 없음') for r in rows}))
    comparison = ''
    if query.compare_kodex:
        daily = [r['bench'].get('069500') for r in rows]
        if all(value is not None for value in daily):
            benchmark = (math.prod(1 + value / 100 for value in daily) - 1) * 100
            difference = result['ret'] * 100 - benchmark
            comparison = f'\n- 같은 기록일 기준 KODEX 200 등락률 {benchmark:+.2f}% · Jev와의 차이 {difference:+.2f}%p'
        else:
            comparison = '\n- KODEX 200의 같은 날짜 등락률 기록이 부족합니다.'
    return (f'### Jev 성과 비교\n\n공유 모의계좌 전체 기준 · 요청 기간 {start} ~ {end} · 기록 {rows[0]["day"]} ~ {rows[-1]["day"]}\n\n'
            f'비교 원장 계산 시각 {_stamp(rows[-1]["built"])} · 원장 출처 {sources}\n\n'
            f'- 손익 {result["pnl"]:+,.0f}원\n- 수익률 {result["ret"] * 100:+.2f}%\n'
            f'- 최대 낙폭 {result["mdd"] * 100:.2f}%\n- 이긴 날 {result["win_days"]}/{result["active"]}일\n'
            f'- 기록 일수 {result["days"]}일{comparison}\n\n수익률은 기간 손익을 일별 최대 투입금의 기간 평균으로 나눈 값입니다.')


def _positions(query: InternalQuery, root: Path, _now: datetime) -> str:
    if query.account == 'live':
        return '**조회할 실계좌를 지정해 주세요.** 예를 들어 “실계좌 1 보유 종목과 잔고는?”이라고 질문하세요.'
    path = root / SHARED / 'account-state.json'
    stamp, holdings = _holdings(path, _revision(path))
    lines = [f'- {code} {name}: {qty:,}주' for code, name, qty in holdings[:30]]
    if len(holdings) > 30:
        lines.append(f'- 전체 {len(holdings)}종목 중 30종목 표시')
    return f'### 공유 모의계좌 보유 스냅숏\n\n저장 기준 {stamp} 한국시간 · 출처 공유 모의계좌 상태\n\n' + (
        '\n'.join(lines) if lines else '기록된 보유 종목이 없습니다.'
    )


def _live_positions(account_key: str, account: dict) -> str:
    from nh_live_broker import orderable_cash, read_linked_balance
    from src.config.loader import load_agent_config
    from src.tools.mcp import MCPServerAdapter

    config = load_agent_config().mcp_servers['nhplug_readonly']
    adapter = MCPServerAdapter('nhplug_readonly', config, interactive_oauth=False)
    balance, observed_at = read_linked_balance(adapter, account_key, account)
    summary = {**balance['Output_0'], 'orr_pbl_amt1': orderable_cash(balance['Output_0'])}
    amounts = []
    for field, title in (('dca', '예수금'), ('orr_pbl_amt1', '주문가능금액'), ('tot_aet_amt', '총자산'),
                         ('tot_eal_amt', '보유 평가금액')):
        value = summary.get(field)
        amount = f'{value:,.0f}원' if type(value) in (int, float) and math.isfinite(value) else '확인 불가'
        amounts.append(f'- {title} {amount}')

    holdings = []
    for row in balance['Output_1']:
        quantity = row.get('rsdl_qty') if isinstance(row, dict) else None
        if not isinstance(quantity, (int, float)) or isinstance(quantity, bool) or not math.isfinite(quantity) or quantity < 0:
            raise ValueError('실계좌 보유 수량 확인 불가')
        if quantity == 0:
            continue
        symbol, name = row.get('iem_cd'), row.get('iem_nm')
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Za-z0-9]{6,7}(?:_(?:AL|NX))?', symbol):
            raise ValueError('실계좌 보유 종목코드 확인 불가')
        label = re.sub(r'[\[\]<>\\\x60*_!~]', '', ' '.join(name.split())) if isinstance(name, str) else ''
        holdings.append(f'- {label} ({symbol}) · {quantity:,}주' if label else f'- {symbol} · {quantity:,}주')

    account_label = f'실계좌 {account_key.removeprefix("live-")}'
    stamp = observed_at.astimezone(KST).strftime('%Y-%m-%d %H:%M:%S')
    return '\n'.join([
        f'### {account_label} 잔고와 보유 종목', '',
        f'조회 완료 {stamp} 한국시간 · 출처 NH 나무 PLUG 읽기 전용 조회', '',
        *amounts, '', '#### 보유 종목', '',
        *(holdings or ['조회된 보유 종목이 없습니다.']),
    ])


def _fill_answer(query: InternalQuery, root: Path, now: datetime) -> str:
    from live_fills import NAME
    from trading_journal import stock_names

    if query.account == 'live':
        return '**실계좌 번호를 지정해 주세요.** 예를 들어 “오늘 실계좌 1 체결 내역은?”이라고 질문하세요.'
    paper = query.account == 'paper'
    path = root / SHARED / 'orders.sqlite3' if paper else root / NAME
    label = '공유 모의 체결 원장' if paper else f'실계좌 {query.account.removeprefix("live-")} 확인 체결 기록'
    revision = _revision(path, wal=True)
    if revision[0] is None:
        return f'### {label}\n\n체결 원장을 확인할 수 없습니다.'
    start, end = bounds(query.period, now.date())
    rows = _fills(path, start, end, query.account, revision)
    names = stock_names({symbol for _, symbol, *_ in rows})
    lines = []
    for stamp, symbol, side, qty, price in rows:
        stamp = f'{stamp[:10]} (시각 미기록)' if not paper and 'T00:00:00' in stamp else _stamp(stamp)
        side = {'BUY': '매수', 'SELL': '매도'}.get(side, side)
        lines.append(f'- {stamp} · {symbol} {names.get(symbol, "")}'.rstrip() + f' · {side} {qty:,}주 · {price:,.0f}원')
    time_basis = '모의 원장의 체결 확인 시각입니다.' if paper else '실계좌 항목의 시각은 원장에 기록된 주문 시각입니다.'
    return f'### {label}\n\n{start} ~ {end} · 최근 최대 20건\n\n{time_basis}\n\n' + (
        '\n'.join(lines) if lines else '해당 기간에 저장된 체결 기록이 없습니다.'
    )


def _status(query: InternalQuery, _root: Path, now: datetime) -> str:
    from automation_status import processes

    states = processes(max_age=0)
    prefix = ''
    if query.account == 'live':
        prefix = '실계좌 '
    elif query.account.startswith('live-'):
        prefix = f'실계좌 {query.account.removeprefix("live-")} '
    rows = '\n'.join(f'- {name} · {state}' for name, state in states.items() if '실행기' in name and name.startswith(prefix))
    return f'### 실행기 상태\n\n조회 요청 {now:%Y-%m-%d %H:%M:%S} 한국시간 · 출처 운영 상태\n\n' + (
        rows or '확인된 실행기 상태가 없습니다.'
    )


READERS = {'performance': _performance, 'positions': _positions, 'fills': _fill_answer, 'status': _status}


def answer(query: InternalQuery, runtime: Path, now: datetime) -> str:
    reader = READERS.get(query.kind)
    if reader is None:
        return ('요청한 기간·계좌·항목의 내부 조회를 지원하지 않습니다. 대시보드에서 확인하거나 '
                '오늘·어제·이번 주·지난주·이번 달·지난달 중 한 기간과 계좌 하나를 골라 다시 질문하세요.')
    root = Path(runtime)
    try:
        if query.account.startswith('live-'):
            from accounts import load

            account = load(root / 'accounts.json').get(query.account, {})
            if account.get('kind') != 'live' or account.get('invalid'):
                return '대시보드에서 해당 실계좌를 확인할 수 없습니다. 계좌·자동매매 화면에서 등록한 번호를 확인하세요.'
            if query.kind == 'positions':
                return _live_positions(query.account, account)
        return reader(query, root, now.astimezone(KST))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
        logger.warning('내부 데이터 조회 오류 · %s · %s', query.kind, type(exc).__name__)
        if query.kind == 'positions' and query.account.startswith('live-'):
            return 'NH에서 실계좌 잔고와 보유 종목을 확인하지 못했습니다. 연결 상태를 확인한 뒤 다시 조회하세요.'
        return '요청한 내부 기록을 읽을 수 없습니다. 대시보드에서 해당 원장과 갱신 상태를 확인하세요.'
