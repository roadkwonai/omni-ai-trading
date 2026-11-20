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
UNSUPPORTED_PERIOD = re.compile(
    r'\d{4}[-./년]|\d{1,2}월|\d{1,2}일|작년|다음[주달]|내일|최근\d|연초|올해|전체기간|지난거래일|직전거래일',
)
Kind = Literal['performance', 'fills', 'positions', 'status', 'unsupported']


@dataclass(frozen=True)
class InternalQuery:
    kind: Kind
    period: str = '오늘'
    account: str = 'paper'
    compare_kodex: bool = False


def resolve(question: str, *, previous: InternalQuery | None = None) -> InternalQuery | None:
    q = ''.join(question.casefold().split()).rstrip('?!.')
    follow_up = q.removeprefix('그럼').removesuffix('는').removesuffix('은')
    if previous and previous.kind in ('performance', 'fills') and follow_up in PERIODS:
        return replace(previous, period=follow_up)
    if previous and previous.kind in ('fills', 'positions'):
        number = re.fullmatch(r'(?:실계좌|계좌)?([1-9][0-9]*)', follow_up)
        if number:
            return replace(previous, account=f'live-{number[1]}')
        if follow_up in ('모의', '모의계좌'):
            return replace(previous, account='paper')
        if previous.kind == 'positions' and previous.account.startswith('live-') and follow_up in (
            '잔고', '예수금', '총자산', '보유종목', '다시조회', '다시조회해줘', '새로고침',
        ):
            return previous
    if any(word in q for word in (
        '어디서', '어디에', '사용법', '뜻', '계산방법', '어떻게계산', '어떻게해석', '어떻게읽',
        '어떻게확인', '어떻게비교', '확인방법', '확인하는법', '확인하는방법', '조회방법', '조회하는법',
        '보는법', '비교할때', '무엇인가요', 'csv', '내려받',
    )):
        return None

    periods = [value for value in PERIODS if value in q]
    period = periods[0] if periods else '오늘'
    accounts = set(re.findall(r'실계좌([1-9][0-9]*)', q))
    live = '실계좌' in q or ('실제체결' in q and '모의' not in q) or ('내계좌' in q and '모의' not in q)
    account = f'live-{next(iter(accounts))}' if accounts else 'live' if live else 'paper'
    if '운영상태' in q or (any(word in q for word in ('실행기', '런치드')) and any(
        word in q for word in ('상태', '돌고', '실행중', '켜져', '멈춰', '운영중')
    )):
        kind: Kind = 'status'
    elif live and any(word in q for word in ('잔고', '잔액', '예수금', '총자산', '주문가능금액')):
        kind = 'positions'
    elif any(word in q for word in ('보유현황', '보유종목', '포지션')) or ('보유' in q and any(
        word in q for word in ('계좌', '몇주', '어떤종목', '있어', '알려', '보여')
    )):
        kind = 'positions'
    elif any(word in q for word in ('체결', '거래내역', '매매내역')) and (
        any(word in q for word in ('계좌', '내역', '기록', '종목', '보여', '알려')) or any(word in q for word in PERIODS)
    ):
        kind = 'fills'
    elif any(name in q for name in ('jev', '제브')) and any(word in q for word in ('성과', '수익', '손익', '낙폭', '성능', '승률')):
        kind = 'performance'
    elif any(word in q for word in ('실계좌', '모의계좌', '내계좌')) and any(
        word in q for word in ('잔고', '수익', '손익', '성과', '잔액', '예수금', '계좌번호')
    ):
        kind = 'unsupported'
    else:
        return None
    if len(periods) > 1 or len(accounts) > 1 or ('실계좌' in q and '모의계좌' in q):
        return InternalQuery('unsupported')
    if UNSUPPORTED_PERIOD.search(q) or (kind in ('status', 'positions') and period != '오늘'):
        return InternalQuery('unsupported')
    if kind == 'performance' and account != 'paper':
        return InternalQuery('unsupported')
    return InternalQuery(kind, period, account, kind == 'performance' and ('kodex200' in q or '069500' in q))


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
    return (f'### Jev 성과 비교\n\n요청 기간 {start} ~ {end} · 기록 {rows[0]["day"]} ~ {rows[-1]["day"]}\n\n'
            f'비교 원장 계산 시각 {rows[-1]["built"]} · 원장 출처 {sources}\n\n'
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
    from nh_live_broker import read_linked_balance
    from src.config.loader import load_agent_config
    from src.tools.mcp import MCPServerAdapter

    config = load_agent_config().mcp_servers['nhplug_readonly']
    adapter = MCPServerAdapter('nhplug_readonly', config, interactive_oauth=False)
    balance, observed_at = read_linked_balance(adapter, account_key, account)
    summary = balance['Output_0']
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
    lines = []
    for stamp, symbol, side, qty, price in rows:
        if not paper and 'T00:00:00' in stamp:
            stamp = f'{stamp[:10]} (시각 미기록)'
        side = {'BUY': '매수', 'SELL': '매도'}.get(side, side)
        lines.append(f'- {stamp} · {symbol} · {side} {qty:,}주 · {price:,.0f}원')
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
        return '요청한 기간 또는 계좌의 내부 조회를 지원하지 않습니다. 대시보드에서 해당 계좌와 기간을 확인하세요.'
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
