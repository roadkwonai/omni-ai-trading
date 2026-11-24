"""What an approved plan may do besides changing a setting: stop, request a trade, restart, start, publish.

Every one needs the person's one-time code (approval.py). `prepare` reads the situation and refuses what cannot be done; it
runs when the plan is made and again right before it is carried out, and a plan whose situation changed is refused. Trades
are the request files the dashboard writes: the paper runner and the live mirror carry them out next cycle through their
own guarded paths. Nothing here talks to a broker's order service.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from . import reads
from . import settings as S
from .output import KST, Refused

TRADE_MINUTES = 10  # a trade plan is for the prices the person just saw


@dataclass(frozen=True)
class Action:
    title: str
    prepare: Callable           # (nh, args) -> ([[what, before, after]], affected accounts); Refused when it cannot be done
    run: Callable               # (nh, args) -> what happened
    minutes: int | None = None  # how long the plan lives (None: as a setting's)
    phrase: Callable | None = None  # (nh, args) -> the words the person types besides the code
    risk: bool = False          # it can move or expose real money: the request says so and the result is reported


def clock():
    return datetime.now(KST)


def live_jev(nh):
    live = S.live_accounts(nh)
    return ['확인 불가'] if live is None else live['jev']


def live_record(nh, key):
    record = S.Accounts().records(nh).get(key)
    if not record or record.get('kind') != 'live' or record.get('invalid'):
        raise Refused(f'실계좌를 찾지 못했습니다: {key}')
    return record


def live_service(key, engine):
    return f'longterm-{key}' if engine == 'longterm' else f"live-follow-{key.removeprefix('live-')}"


def service_up(name, label):
    import os_services
    try:
        if os_services.state(name)['running']:
            return
    except os_services.Unknown:
        raise Refused(f'{label} 상태를 확인할 수 없습니다.', code=6) from None
    raise Refused(f'{label}가 실행 중이 아닙니다. 지금 요청하면 다음 시작 때 처리되므로 받지 않습니다.')


# --- trades -------------------------------------------------------------------------------------------------------------------

PAPER = {'sell': 'exit-requests.json', 'buy': 'manual-buys.json'}


class AllHeld:
    def __contains__(self, code):  # reading never prunes the runner's file
        return True


def paper_pending(nh, side):
    from nh_paper_isolated import exit_requests, manual_buys
    root = nh / 'shared-etf-paper'
    return manual_buys(root) if side == 'buy' else exit_requests(root, AllHeld())


def live_pending(nh, key, side):
    from nh_live_follow import exit_requests, manual_buy_requests
    return manual_buy_requests(nh, key) if side == 'buy' else exit_requests(nh, key)


def paper_held(nh):
    from engine_compare import account_state
    state = account_state(nh / 'shared-etf-paper', datetime.now(KST), max_age=timedelta(minutes=10))
    if state is None:
        raise Refused('모의 실행기의 최근 10분 잔고 기록이 없습니다. 실행기가 실행 중인 장중에만 요청할 수 있습니다.', code=5)
    return {c: int(h['rsdl_qty']) for c, h in state['holdings'].items() if type(h.get('rsdl_qty')) is int and h['rsdl_qty'] > 0}


def live_held(nh, key, record):
    """The shares the mirror itself holds (its own ledger); needs the account number, which stays in memory."""
    import accounts
    from ..utils.omni_backend import readonly
    from nh_live_follow import ledger_owned
    try:
        got = readonly().call_tool('list_accounts', {})
        number = accounts.resolve({key: record}, (got.get('data') or {}).get('Output_0') or []).get(key)
        return ledger_owned(number, nh)[1] if number else None
    except Exception as exc:  # noqa: BLE001 - a broker message may hold an account
        raise Refused(f'미러 보유를 확인하지 못했습니다(조회 실패: {type(exc).__name__}).', code=6) from None


def claimed(nh, where):
    return any(p.with_name(p.name + '.claim').exists() for p in cancel_files(nh, where).values())


def rewrite(path, change):
    """Write `change(pending)` unless the runner or the dashboard wrote the file meanwhile."""
    from nh_live_follow import write_request_file
    def raw():
        return path.read_bytes() if path.exists() else None
    before = raw()
    after = change()
    if raw() != before:
        raise Refused('요청 파일이 방금 바뀌었습니다. 다시 계획하세요.')
    write_request_file(path, after)


def wanted(code, qty, held, pending, side):
    if code not in held:
        raise Refused(f'보유하지 않은 종목입니다: {code}. 보유: {", ".join(sorted(held)) or "없음"}')
    if code in pending:
        raise Refused(f'{code}은 이미 대기 중인 요청이 있습니다.')
    if side == 'sell' and qty is not None and qty > held[code]:
        raise Refused(f'{code} 보유는 {held[code]:,}주입니다.')
    return f'{"매도" if side == "sell" else "추가 매수"} {"전량" if qty is None else f"{qty:,}주"}'


def paper_trade(side):
    def prepare(nh, a):
        service_up('shared-etf-paper', '공유 모의 실행기')
        held = paper_held(nh)
        text = wanted(a['code'], a['qty'], held, {**paper_pending(nh, 'sell'), **paper_pending(nh, 'buy')}, side)
        return [[f"모의 {a['code']}", f"보유 {held[a['code']]:,}주", text]], live_jev(nh) or ['모의계좌']

    def run(nh, a):
        rewrite(nh / 'shared-etf-paper' / PAPER[side], lambda: {**paper_pending(nh, side), a['code']: a['qty']})
        return {'filed': True, 'when': '공유 모의 실행기의 다음 회차(약 1분 이내), 주문 가드를 거침'}
    return prepare, run


def live_mirror(nh, key):
    """(record, {code: shares the mirror holds}) of a Jev live account that takes requests now."""
    record = live_record(nh, key)
    if record['engine'] != 'jev':
        raise Refused('Jev 미러 실계좌만 매매 요청을 받습니다. 장기투자 실계좌는 엔진 계획을 따릅니다.')
    if record.get('live_orders') is not True:  # a request kept by a plan-only mirror could trade when the switch is turned on later
        raise Refused('실주문 스위치가 꺼져 있어 매매 요청을 받지 않습니다.')
    if record['auto'] is not True:  # the mirror takes no request then, and would carry it out days later
        raise Refused('이 계좌의 자동매매가 꺼져 있어 매매 요청을 받지 않습니다.')
    service_up(live_service(key, 'jev'), '실계좌 미러')
    if claimed(nh, key):
        raise Refused('미러가 지금 처리 중인 요청이 있습니다. 다음 회차 뒤 다시 하세요.')
    held = live_held(nh, key, record)
    if held is None:
        raise Refused('연결된 계좌 목록에서 이 실계좌를 찾지 못해 미러 보유를 확인하지 못했습니다.', code=6)
    return record, held


def live_sell_prepare(nh, a):
    _, held = live_mirror(nh, a['account'])
    text = wanted(a['code'], a['qty'], held, {**live_pending(nh, a['account'], 'sell'), **live_pending(nh, a['account'], 'buy')}, 'sell')
    return [[f"{a['account']} {a['code']}", f"미러 보유 {held[a['code']]:,}주", text]], [a['account']]


def live_sell_run(nh, a):
    from nh_live_follow import exit_request_path
    rewrite(exit_request_path(nh, a['account']), lambda: {**live_pending(nh, a['account'], 'sell'), a['code']: a['qty']})
    return {'filed': True, 'when': '실계좌 미러의 다음 회차(약 1분 이내), 실계좌 가드를 거침'}


def cancel_files(nh, where):
    from nh_live_follow import exit_request_path, manual_buy_path
    return {s: nh / 'shared-etf-paper' / n for s, n in PAPER.items()} if where == 'paper' else {'sell': exit_request_path(nh, where), 'buy': manual_buy_path(nh, where)}


def cancel_prepare(nh, a):
    where, code = a['where'], a['code']
    if where != 'paper':
        live_record(nh, where)
    if claimed(nh, where):
        raise Refused('미러가 지금 처리 중인 요청이 있어 취소할 수 없습니다.')
    read = paper_pending if where == 'paper' else (lambda nh, side: live_pending(nh, where, side))
    found = [[f'{where} {code} {"매도" if side == "sell" else "매수"} 요청', '전량' if q[code] is None else f'{q[code]:,}주', '취소'] for side in ('sell', 'buy') if code in (q := read(nh, side))]
    if not found:
        raise Refused('대기 중인 요청이 없습니다.')
    return found, (live_jev(nh) or ['모의계좌']) if where == 'paper' else [where]


def cancel_run(nh, a):
    read = (lambda side: paper_pending(nh, side)) if a['where'] == 'paper' else (lambda side: live_pending(nh, a['where'], side))
    gone = []
    for side, path in cancel_files(nh, a['where']).items():
        if a['code'] in read(side):
            rewrite(path, lambda: {c: q for c, q in read(side).items() if c != a['code']})
            gone.append(side)
    if not gone or claimed(nh, a['where']):
        raise Refused('실행기나 미러가 이미 가져가 취소하지 못했습니다. 주문 기록을 확인하세요.', code=1)
    return {'cancelled': gone}


# --- stops and services ---------------------------------------------------------------------------------------------------------

def live_off_prepare(nh, a):
    record = live_record(nh, a['account'])
    if record.get('live_orders') is not True:
        raise Refused('이미 실주문이 꺼져 있습니다.')
    return [[f"accounts.{a['account']}.live_orders", True, False]], [a['account']]


def live_off_run(nh, a):
    import accounts
    accounts.set_live_orders(a['account'], False, nh / 'accounts.json', who=a['who'])
    if accounts.load(nh / 'accounts.json')[a['account']].get('live_orders') is not False:
        raise RuntimeError
    return {}


def halt_prepare(nh, a):
    from src.live.halt import halt_flag_set
    if halt_flag_set('nhplug'):
        raise Refused('이미 HALT가 걸려 있습니다.')
    live = S.live_accounts(nh) or {}
    return [['HALT', False, True]], sorted({k for v in live.values() for k in v}) or ['모든 실계좌']


def halt_run(nh, a):
    from src.live.halt import halt_flag_set, trip_halt
    trip_halt('cli', a['who'])
    if not halt_flag_set('nhplug'):
        raise RuntimeError
    return {}


def paper_stop_prepare(nh, a):
    service_up('shared-etf-paper', '공유 모의 실행기')
    if (nh / 'shared-etf-paper/STOP').exists():
        raise Refused('이미 중단 요청이 있습니다.')
    return [['공유 모의 실행기 STOP', False, True]], live_jev(nh) or ['모의계좌']


def paper_stop_run(nh, a):
    from automation_status import stop_shared_paper
    stop_shared_paper(nh.parent)
    return {'stop_file': (nh / 'shared-etf-paper/STOP').exists(), 'restart': '미확인 주문을 대조한 뒤 `omni jev unstop`으로 해제합니다.'}


def paper_restart_prepare(nh, a):
    import restart_runner as R
    service_up('shared-etf-paper', '공유 모의 실행기')
    folder = nh / 'shared-etf-paper'
    if (folder / 'STOP').exists():
        raise Refused('이미 중단 요청이 있습니다.')
    if n := R.unconfirmed_orders(folder):
        raise Refused(f'미확인 주문이 {n}건 있습니다. 먼저 대조하세요.')
    if near := R.near_stops(folder, 0.5):
        raise Refused(f'손절선에 가까운 보유가 있습니다: {", ".join(near)}')
    return [['공유 모의 실행기', '실행 중', '다시 시작(손절 감시가 약 10~40초 멈춤)']], live_jev(nh) or ['모의계좌']


def paper_restart_run(nh, a):
    import restart_runner as R
    got = R.restart('shared', a['who'], root=nh)
    if got.get('result') != 'restarted':
        raise Refused(f"다시 시작하지 못했습니다: {got.get('why')}", code=1)
    return {k: got[k] for k in ('result', 'first_cycle', 'watch_paused_seconds') if k in got}


def day_start_prepare(nh, a):
    import os_services
    import trading_day_start as D
    try:
        runner = D.running('shared-etf-paper')
    except os_services.Unknown:
        raise Refused('서비스 상태를 확인할 수 없습니다.', code=6) from None
    now = clock()
    in_start_window(now)
    open_day, note = D.trading_day(now)
    decision = D.decide(open_day, (nh / 'shared-etf-paper/STOP').exists(), runner)
    if decision != 'start':
        raise Refused({'skip_closed': f'거래일이 아닙니다({note}).', 'skip_stop_file': 'STOP 파일이 있습니다.', 'already_running': '이미 실행 중입니다.'}[decision])
    return [['거래일 시작(모의 실행기·학습·국면 관찰기)', '멈춤', '시작']], live_jev(nh) or ['모의계좌']


def day_start_run(nh, a):
    import json
    from ..utils import omni_backend as backend
    code, out, _ = backend.script('trading_day_start.py', timeout=500)
    try:
        got = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError from None
    if code or got.get('decision') != 'start' or not all((got.get('running_after') or {'': False}).values()):
        raise Refused(f"시작을 확인하지 못했습니다: {got.get('decision')} {got.get('running_after')}", code=1)
    return {'decision': got['decision'], 'running_after': got['running_after']}


def orders_run_by(service):
    """Whether this registered service may send orders (paper or live)."""
    import launchd_services as L
    return '--execute' in L.SERVICES[service][0]


def in_start_window(now):
    import session_times
    if not session_times.shifted('08:00', now.date()) <= now.strftime('%H:%M') < session_times.shifted('15:00', now.date()):
        raise Refused('주문 서비스 시작은 08:00~15:00에만 합니다.')


def service_start_prepare(nh, a):
    import launchd_services as L
    import os_services
    name = a['service']
    if name not in L.SERVICES or name == 'trading-day-start':
        raise Refused(f"시작할 수 없는 이름입니다: {name}. 거래일 시작은 `omni ops day-start`입니다.")
    try:
        found = os_services.state(name)
    except os_services.Unknown:
        raise Refused('서비스 상태를 확인할 수 없습니다.', code=6) from None
    if not found['registered']:
        raise Refused('등록되지 않은 서비스입니다. 서비스는 직접 등록해야 합니다.')
    if found['running']:
        raise Refused('이미 실행 중입니다.')
    if not orders_run_by(name):
        return [[f'서비스 {name}', '멈춤', '시작']], ['서비스']
    import trading_day_start as D
    in_start_window(clock())
    open_day, note = D.trading_day(clock())
    if not open_day:
        raise Refused(f'거래일이 아닙니다({note}).')
    if name == 'shared-etf-paper':
        if (nh / 'shared-etf-paper/STOP').exists():
            raise Refused('STOP 파일이 있습니다. 먼저 `omni jev unstop`을 쓰세요.')
        settled(paper_unresolved(nh))
        return [[f'서비스 {name}', '멈춤', '시작(모의 주문)']], live_jev(nh) or ['모의계좌']
    settled(live_unresolved(nh))
    return [[f'서비스 {name}', '멈춤', '시작(실주문 스위치·가드는 그대로)']], [k for k in live_keys(nh) if name.endswith(k.removeprefix('live-'))] or ['실계좌']


UP_WAIT, UP_LOOKS = 1.0, 8  # seconds between looks at a service that was just started


def service_start_run(nh, a):
    import time
    import os_services
    if not os_services.start(a['service']):
        raise RuntimeError
    for _ in range(UP_LOOKS):
        time.sleep(UP_WAIT)
        try:
            if os_services.state(a['service'])['running']:
                return {'running': True}
        except os_services.Unknown:
            continue
    raise Refused('시작을 요청했지만 실행 중인 것을 확인하지 못했습니다. 일정 작업은 할 일이 없으면 바로 끝납니다. `omni ops logs`로 확인하세요.', code=1)


def lt_stop_prepare(on):
    def prepare(nh, a):
        exists = (nh / 'vibe-longterm/STOP').exists()
        if exists is on:
            raise Refused('이미 중지 요청이 있습니다.' if on else '중지 요청이 없습니다.')
        return [['장기 엔진 중지 요청', not on, on]], (S.live_accounts(nh) or {}).get('longterm') or ['장기투자 가상 계좌']
    return prepare


def lt_stop_run(on):
    def run(nh, a):
        stop = nh / 'vibe-longterm/STOP'
        if on:
            stop.write_text(datetime.now(KST).isoformat(), encoding='utf-8')
        else:
            stop.unlink(missing_ok=True)
        return {'stop': stop.exists()}
    return run


# --- what raises the risk: lifting a stop, turning real orders on, starting an order service, restoring a backup ------------------

def live_unresolved(nh):
    """Live orders the guard has not settled; Refused when its ledger cannot be read (that is not "none")."""
    import sqlite3
    try:
        return reads.rows(nh / 'live-orders.sqlite3', "SELECT count(*) AS n FROM live_intents WHERE state!='terminal' "
                          "AND fingerprint NOT IN (SELECT fingerprint FROM live_pre_send_aborts)")[0]['n']
    except (sqlite3.Error, IndexError):
        raise Refused('실주문 기록을 읽지 못해 확인할 수 없습니다.') from None


def paper_unresolved(nh):
    import sqlite3
    try:
        return reads.rows(nh / 'shared-etf-paper/orders.sqlite3', "SELECT count(*) AS n FROM intents WHERE status!='terminal'")[0]['n']
    except (sqlite3.Error, IndexError):
        raise Refused('모의 주문 원장을 읽지 못해 확인할 수 없습니다.') from None


def loss_latched(nh):
    return bool(reads.rows(nh / 'live-orders.sqlite3', 'SELECT 1 FROM live_halts WHERE day=?', (clock().strftime('%Y%m%d'),)))


def no_loss_latch(nh):
    if loss_latched(nh):
        raise Refused('오늘 하루 손실 정지가 걸린 계좌가 있습니다.')


def settled(n):
    if n:
        raise Refused(f'미확인 주문이 {n}건 있습니다. 먼저 대조하세요.')


def live_keys(nh):
    return sorted({k for v in (S.live_accounts(nh) or {}).values() for k in v}) or ['모든 실계좌']


def live_on_phrase(nh, a):
    return f"{live_record(nh, a['account']).get('label', a['account'])} 실주문"


def live_on_prepare(nh, a):
    import os_services
    from src.live.halt import halt_flag_set
    record = live_record(nh, a['account'])
    if record.get('live_orders') is True:
        raise Refused('이미 실주문이 켜져 있습니다.')
    service = live_service(a['account'], record['engine'])
    try:
        armed = '실주문 대기' if '--execute' in (os_services.registered_args(service) or []) else '계획만'
    except os_services.Unknown:
        armed = '확인 불가'
    return [[f"accounts.{a['account']}.live_orders", False, True],
            ['지금 상태', f"자동매매 {'켜짐' if record['auto'] else '꺼짐'} · 실행기 {armed} · HALT {'걸림' if halt_flag_set('nhplug') else '없음'}", '-']], [a['account']]


def live_on_run(nh, a):
    import accounts
    accounts.set_live_orders(a['account'], True, nh / 'accounts.json', who=a['who'])
    if accounts.load(nh / 'accounts.json')[a['account']].get('live_orders') is not True:
        raise RuntimeError
    return {}


def live_resume_prepare(nh, a):
    from nh_live_buy_control import MARKER, buy_allowed
    if buy_allowed(nh):
        raise Refused('신규 매수 중단 상태가 아닙니다.')
    if (nh / 'LIVE_BUY_PAUSED').read_text(encoding='utf-8') != MARKER:
        raise Refused('다른 경로에서 건 중단이라 해제하지 않습니다.')
    settled(live_unresolved(nh))
    no_loss_latch(nh)
    return [['실계좌 신규 매수', '중단', '재개']], live_keys(nh)


def live_resume_run(nh, a):
    from nh_live_buy_control import buy_allowed, resume
    resume(nh)
    if not buy_allowed(nh):
        raise RuntimeError
    return {}


def live_unhalt_prepare(nh, a):
    from src.live.halt import read_halt
    both, only = read_halt(), read_halt('nhplug')
    if both is None and only is None:
        raise Refused('HALT가 없습니다.')
    if both is not None and only is not None:
        raise Refused('전체 HALT와 NH HALT가 함께 걸려 있습니다. 직접 원인을 확인하세요.')
    settled(live_unresolved(nh))
    words = lambda h: ' · '.join(str(h.get(k)) for k in ('tripped_at', 'by', 'reason') if h.get(k))  # noqa: E731
    return [['NH 주문 HALT', words(both or only or {}) or '기록 없음', '해제(다른 증권사 정지는 유지)']], live_keys(nh)


def live_unhalt_run(nh, a):
    import nh_live_activation
    from src.live.halt import clear_halt, halt_flag_set, read_halt
    both = read_halt()
    if both is not None:
        nh_live_activation.resume_nh(both)
    else:
        clear_halt('nhplug')
    if halt_flag_set('nhplug'):
        raise RuntimeError
    return {}


def paper_unstop_prepare(nh, a):
    import os_services
    if not (nh / 'shared-etf-paper/STOP').exists():
        raise Refused('중단 요청이 없습니다.')
    try:
        if os_services.state('shared-etf-paper')['running']:
            raise Refused('실행기가 아직 멈추지 않았습니다. 잠시 뒤 다시 하세요.')
    except os_services.Unknown:
        raise Refused('서비스 상태를 확인할 수 없습니다.', code=6) from None
    settled(paper_unresolved(nh))
    last = reads.tail(nh / 'shared-etf-paper/events.jsonl', 1, None, lambda r: r.get('event') == 'stopped')
    left = (last[0].get('remaining') if last else None) or '없음'
    return [['공유 모의 실행기 STOP', '있음', '지움'], ['멈출 때 남은 보유', left, '-']], live_jev(nh) or ['모의계좌']


def paper_unstop_run(nh, a):
    (nh / 'shared-etf-paper/STOP').unlink()
    return {'next': '실행기는 시작하지 않았습니다. `omni ops start shared-etf-paper` 또는 다음 거래일 시작을 쓰세요.'}


def live_buy_prepare(nh, a):
    from nh_live_buy_control import buy_allowed
    from nh_live_follow import buy_window_end
    from src.live.halt import halt_flag_set
    record, held = live_mirror(nh, a['account'])
    if not buy_allowed(nh) or halt_flag_set('nhplug'):
        raise Refused('실계좌 신규 매수가 중단돼 있거나 HALT가 걸려 있습니다.')
    if clock().strftime('%H:%M') >= buy_window_end(nh):
        raise Refused(f'실계좌 매수 시간이 끝났습니다({buy_window_end(nh)}).')
    settled(live_unresolved(nh))
    no_loss_latch(nh)
    text = wanted(a['code'], a['qty'], held, {**live_pending(nh, a['account'], 'sell'), **live_pending(nh, a['account'], 'buy')}, 'buy')
    import accounts
    caps = f"운용금 {record['budget']['capital_krw']:,}원 · 회당 {accounts.live_limits(record=record)['order_max_krw']:,}원 · 1주 {record['budget']['order_price_max_krw']:,}원 · {buy_window_end(nh)}까지"
    return [[f"{a['account']} {a['code']}", f"미러 보유 {held[a['code']]:,}주", text], ['이 안에서 회차마다 나눠 삽니다', caps, '-']], [a['account']]


def live_buy_run(nh, a):
    from nh_live_follow import manual_buy_path
    rewrite(manual_buy_path(nh, a['account']), lambda: {**live_pending(nh, a['account'], 'buy'), a['code']: a['qty']})
    return {'filed': True, 'when': '실계좌 미러의 다음 회차(약 1분 이내), 실계좌 가드를 거침. 추가 매수분은 정리 시각에 함께 팝니다.'}


def backup_path(name):
    import state_sync as ss
    found = next((p for p in ss.archives(ss.STATE / 'backups') if p.name == name), None)
    if found is None:
        raise Refused('해당 백업이 없습니다. `omni backup list`로 확인하세요.', code=5)
    return found


def backup_import_prepare(nh, a):
    import state_sync as ss
    running = ss.running_services()
    if running is None:
        raise Refused('서비스 상태를 확인할 수 없습니다.', code=6)
    if running:
        raise Refused('서비스가 실행 중이라 가져올 수 없습니다: ' + ', '.join(r.removeprefix('com.vibetrading.') for r in running))
    path = backup_path(a['name'])
    if problems := ss.verify(path):
        raise Refused('백업 검증에 실패했습니다: ' + '; '.join(problems[:3]))
    settled(live_unresolved(nh))
    settled(paper_unresolved(nh))
    no_loss_latch(nh)  # the restored records would not hold today's latch
    manifest = ss.read_manifest(path)
    now, then = reads.records(nh), archived_accounts(path)
    flips = [[f'accounts.{k}.{f}', now.get(k, {}).get(f), r.get(f)] for k, r in sorted(then.items()) for f in ('live_orders', 'auto', 'engine') if now.get(k, {}).get(f) != r.get(f)]
    live = sorted({k for src in (now, then) for k, r in src.items() if r.get('live_orders') is True})
    return [[f"백업 {a['name']}", f"{manifest['created']} · 파일 {len(manifest['files'])}개", '지금 기록을 이 백업으로 바꿈(지금 것은 따로 보관)'], *flips], [*live, '모든 기록']


def archived_accounts(path):
    """The accounts of a backup, to show which switches a restore would flip."""
    import json
    import tarfile
    with tarfile.open(path, 'r:gz') as tar:
        member = next((m for m in tar if m.isfile() and m.name.endswith('/nhplug/accounts.json')), None)
        found = json.loads(tar.extractfile(member).read()) if member else {}
    records = found.get('accounts') if isinstance(found, dict) else None
    return {k: r for k, r in records.items() if isinstance(r, dict)} if isinstance(records, dict) else {}


def backup_import_run(nh, a):
    import state_sync as ss
    try:
        got = ss.import_archive(backup_path(a['name']), nh.parent)
    except SystemExit as exc:
        raise Refused(str(exc), code=1) from None
    return {'restored': got['restored'], 'moved_aside': got['moved_aside'], 'aside': str(got['aside']).rsplit('/', 1)[-1]}


# --- sending outside --------------------------------------------------------------------------------------------------------------

def report_prepare(what):
    def prepare(nh, a):
        found = reads.rows(nh / 'report-pages.sqlite3', 'SELECT title, publish_status, send_status FROM reports WHERE id=?', (a['report'],))
        if not found:
            raise Refused('발행 기록에 해당 리포트가 없습니다. `omni report pages`로 확인하세요.', code=5)
        row = found[0]
        return [[f"리포트 {a['report']} {row['title'] or ''}".strip(), row['publish_status' if what == 'publish' else 'send_status'], '공개 페이지에 다시 게시' if what == 'publish' else '알림 채널로 다시 발송']], ['외부 공개' if what == 'publish' else '알림 채널']
    return prepare


def report_run(what):
    def run(nh, a):
        import report_pages
        return {'result': report_pages.retry(what, a['report'])}
    return run


def test_send_prepare(nh, a):
    return [['알림 채널 연결 확인', None, '시험 메시지 발송']], ['알림 채널']


def test_send_run(nh, a):
    import discord_notify as N
    return {'sent': {channel: {k: bool(v) for k, v in N.send_everywhere(targets, '✅ 연결 확인 · Omni AI Trading 알림').items()} if targets else '받는 곳 없음'
                     for channel, targets in N.urls_from_env().items()}}


ACTIONS = {
    'live_off': Action('실주문 끄기(보유 손절 주문도 나가지 않습니다)', live_off_prepare, live_off_run),
    'live_on': Action('실주문 켜기(이 계좌에 실제 주문이 나갑니다)', live_on_prepare, live_on_run, phrase=live_on_phrase, risk=True),
    'halt': Action('HALT 걸기(모든 실주문이 막히며 손절 주문도 나가지 않습니다)', halt_prepare, halt_run),
    'live_unhalt': Action('NH 주문 HALT 해제', live_unhalt_prepare, live_unhalt_run, phrase=lambda nh, a: 'HALT 해제', risk=True),
    'live_resume': Action('실계좌 신규 매수 재개', live_resume_prepare, live_resume_run, risk=True),
    'paper_sell': Action('모의 매도 요청(실계좌 미러도 비율대로 따라 팝니다)', *paper_trade('sell'), TRADE_MINUTES),
    'paper_buy': Action('모의 추가 매수 요청(실계좌 미러도 따라 삽니다)', *paper_trade('buy'), TRADE_MINUTES),
    'live_sell': Action('실계좌 매도 요청(미러가 산 수량만)', live_sell_prepare, live_sell_run, TRADE_MINUTES),
    'live_buy': Action('실계좌 추가 매수 요청(미러 보유 종목만)', live_buy_prepare, live_buy_run, TRADE_MINUTES, risk=True),
    'request_cancel': Action('매매 요청 취소', cancel_prepare, cancel_run, TRADE_MINUTES),
    'paper_stop': Action('공유 모의 실행기 중단(멈춘 동안 손절·익절 감시도 없습니다)', paper_stop_prepare, paper_stop_run),
    'paper_unstop': Action('공유 모의 실행기 중단 해제(시작하지는 않습니다)', paper_unstop_prepare, paper_unstop_run, risk=True),
    'paper_restart': Action('공유 모의 실행기 다시 시작', paper_restart_prepare, paper_restart_run, TRADE_MINUTES),
    'day_start': Action('거래일 시작', day_start_prepare, day_start_run, TRADE_MINUTES),
    'service_start': Action('서비스 시작', service_start_prepare, service_start_run, TRADE_MINUTES),
    'lt_stop': Action('장기 엔진 중지 요청(가상 계좌의 다음 단계부터 멈춤, 장기투자 실계좌의 오늘 주문은 계속)', lt_stop_prepare(True), lt_stop_run(True)),
    'lt_unstop': Action('장기 엔진 중지 요청 해제', lt_stop_prepare(False), lt_stop_run(False)),
    'report_publish': Action('리포트 다시 게시', report_prepare('publish'), report_run('publish')),
    'report_send': Action('리포트 다시 발송', report_prepare('send'), report_run('send')),
    'notify_test': Action('알림 시험 발송', test_send_prepare, test_send_run),
    'backup_import': Action('백업 가져오기(지금 설정과 기록을 바꿉니다)', backup_import_prepare, backup_import_run, phrase=lambda nh, a: '백업 가져오기', risk=True),
}


def risky(name, args):
    return ACTIONS[name].risk or (name == 'service_start' and orders_run_by(args['service']))
